import io
import re
import unittest
from unittest.mock import patch

import app


class ExtractIdsTest(unittest.TestCase):
    def test_extracts_json_array_even_with_surrounding_prose(self):
        text = "Sure, here you go:\n```json\n[3, 7, 12]\n```\nHope that helps!"
        match = re.search(r"\[.*\]", text, re.S)
        self.assertEqual(app.json.loads(match.group(0)), [3, 7, 12])

    def test_unknown_ids_are_dropped(self):
        foods = {1: "Milk", 2: "Eggs"}
        ids = [i for i in [1, 2, 999] if i in foods]
        self.assertEqual(ids, [1, 2])


class ConfiguredProvidersTest(unittest.TestCase):
    def test_only_providers_with_a_key_set_are_listed(self):
        with patch.dict(app.os.environ, {"OPENAI_API_KEY": "sk-x"}, clear=True):
            self.assertEqual(app.configured_providers(), [{"value": "openai", "label": "OpenAI"}])

    def test_none_configured_gives_empty_list(self):
        with patch.dict(app.os.environ, {}, clear=True):
            self.assertEqual(app.configured_providers(), [])


class NormalizeTandoorUrlTest(unittest.TestCase):
    def test_bare_hostname_gets_https_prefix(self):
        self.assertEqual(app.normalize_tandoor_url("recipes.example.com"), "https://recipes.example.com")

    def test_explicit_scheme_is_left_alone(self):
        self.assertEqual(app.normalize_tandoor_url("http://localhost:8080"), "http://localhost:8080")
        self.assertEqual(
            app.normalize_tandoor_url("https://recipes.example.com"), "https://recipes.example.com"
        )

    def test_trailing_slash_is_stripped(self):
        self.assertEqual(app.normalize_tandoor_url("recipes.example.com/"), "https://recipes.example.com")

    def test_blank_stays_blank(self):
        self.assertEqual(app.normalize_tandoor_url(""), "")


class VisionProviderTest(unittest.TestCase):
    def test_anthropic_request_and_response_shape(self):
        url, headers, body = app._build_vision_request(
            "anthropic", "claude-sonnet-5", "sk-ant-x", "QUJD", "image/png", "hi"
        )
        self.assertIn("api.anthropic.com", url)
        self.assertEqual(headers["x-api-key"], "sk-ant-x")
        image_block = body["messages"][0]["content"][0]
        self.assertEqual(image_block["source"]["data"], "QUJD")
        self.assertEqual(app._extract_vision_text("anthropic", {"content": [{"text": "[1, 2]"}]}), "[1, 2]")

    def test_openai_image_url_is_nested_under_url_key(self):
        url, headers, body = app._build_vision_request(
            "openai", "gpt-4o-mini", "sk-oa-x", "QUJD", "image/jpeg", "hi"
        )
        self.assertIn("api.openai.com", url)
        image_block = body["messages"][0]["content"][1]
        self.assertEqual(image_block["image_url"]["url"], "data:image/jpeg;base64,QUJD")
        self.assertEqual(
            app._extract_vision_text("openai", {"choices": [{"message": {"content": "[1]"}}]}), "[1]"
        )

    def test_mistral_image_url_is_a_bare_string(self):
        url, headers, body = app._build_vision_request(
            "mistral", "pixtral-12b-2409", "sk-mi-x", "QUJD", "image/jpeg", "hi"
        )
        self.assertIn("api.mistral.ai", url)
        image_block = body["messages"][0]["content"][1]
        self.assertEqual(image_block["image_url"], "data:image/jpeg;base64,QUJD")

    def test_missing_api_key_raises_before_any_network_call(self):
        with patch.dict(app.os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                app.call_vision_llm("openai", b"x", "image/png", "hi")


class PickRecipesTest(unittest.TestCase):
    def test_uses_makenow_when_it_fills_all_slots(self):
        with patch.object(app, "tandoor") as t:
            t.return_value = (200, {"results": [{"id": 1}, {"id": 2}]})
            result, ingredients_by_id = app.pick_recipes("http://x", "tok", 2)
            self.assertEqual([r["id"] for r in result], [1, 2])
            self.assertEqual(ingredients_by_id, {})
            t.assert_called_once()  # only the makenow lookup, no fallback scan

    def test_falls_back_to_ranking_by_onhand_fraction(self):
        def fake_tandoor(base, token, method, path, params=None, body=None):
            if params and params.get("makenow"):
                return 200, {"results": []}
            if path == "/api/recipe/":
                return 200, {"results": [{"id": 1, "servings": 2}, {"id": 2, "servings": 2}]}
            if path == "/api/recipe/1/":
                return 200, {
                    "steps": [
                        {
                            "ingredients": [
                                {"food": {"name": "A", "food_onhand": True}},
                                {"food": {"name": "B", "food_onhand": False}},
                            ]
                        }
                    ]
                }
            if path == "/api/recipe/2/":
                return 200, {"steps": [{"ingredients": [{"food": {"name": "A", "food_onhand": True}}]}]}
            raise AssertionError(path)

        with patch.object(app, "tandoor", side_effect=fake_tandoor):
            result, ingredients_by_id = app.pick_recipes("http://x", "tok", 5)
            # recipe 2 is 100% on-hand, recipe 1 is 50% -> recipe 2 ranks first
            self.assertEqual([r["id"] for r in result], [2, 1])
            self.assertEqual(len(ingredients_by_id[1]), 2)
            self.assertEqual(len(ingredients_by_id[2]), 1)


class BuildSlotsTest(unittest.TestCase):
    def test_cycles_recipes_across_days_and_meals(self):
        recipes = [{"id": 1, "name": "A", "servings": 2}, {"id": 2, "name": "B", "servings": 3}]
        slots = app.build_slots(recipes, days=2, meals_per_day=2)
        self.assertEqual([s["recipe_id"] for s in slots], [1, 2, 1, 2])
        self.assertEqual(slots[0]["date"], slots[1]["date"])
        self.assertNotEqual(slots[0]["date"], slots[2]["date"])

    def test_empty_recipes_gives_no_slots(self):
        self.assertEqual(app.build_slots([], days=3, meals_per_day=1), [])


class ApiPlanPreviewTest(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_falls_back_to_env_when_form_fields_are_blank(self):
        with patch.dict(
            app.os.environ,
            {"TANDOOR_URL": "http://env-tandoor", "TANDOOR_TOKEN": "env-tok", "ANTHROPIC_API_KEY": "sk-x"},
        ):
            with patch.object(app, "propose_plan", return_value={"ok": True}) as propose_plan:
                resp = self.client.post(
                    "/api/plan/preview",
                    data={"image": (io.BytesIO(b"x"), "fridge.png")},
                    content_type="multipart/form-data",
                )
                self.assertEqual(resp.status_code, 200)
                propose_plan.assert_called_once()
                _, base_url, token = propose_plan.call_args[0][:3]
                self.assertEqual(base_url, "http://env-tandoor")
                self.assertEqual(token, "env-tok")

    def test_form_fields_take_priority_over_env(self):
        with patch.dict(
            app.os.environ,
            {"TANDOOR_URL": "http://env-tandoor", "TANDOOR_TOKEN": "env-tok", "ANTHROPIC_API_KEY": "sk-x"},
        ):
            with patch.object(app, "propose_plan", return_value={"ok": True}) as propose_plan:
                self.client.post(
                    "/api/plan/preview",
                    data={
                        "tandoor_url": "http://form-tandoor",
                        "api_token": "form-tok",
                        "image": (io.BytesIO(b"x"), "fridge.png"),
                    },
                    content_type="multipart/form-data",
                )
                _, base_url, token = propose_plan.call_args[0][:3]
                self.assertEqual(base_url, "http://form-tandoor")
                self.assertEqual(token, "form-tok")

    def test_missing_both_form_and_env_is_a_400(self):
        with patch.dict(app.os.environ, {}, clear=True):
            resp = self.client.post(
                "/api/plan/preview",
                data={"image": (io.BytesIO(b"x"), "fridge.png")},
                content_type="multipart/form-data",
            )
            self.assertEqual(resp.status_code, 400)


class ApiPlanConfirmTest(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_creates_meal_plan_entries_and_dedupes_shopping_list_calls(self):
        calls = []

        def fake_tandoor(base, token, method, path, params=None, body=None):
            calls.append((method, path, body))
            if path == "/api/meal-type/":
                return 200, {"results": [{"id": 7}]}
            if path == "/api/meal-plan/":
                return 201, {"id": len(calls)}
            if path.endswith("/shopping/"):
                return 200, {}
            raise AssertionError(path)

        with patch.object(app, "tandoor", side_effect=fake_tandoor):
            resp = self.client.post(
                "/api/plan/confirm",
                json={
                    "tandoor_url": "http://x",
                    "api_token": "tok",
                    "slots": [
                        {"date": "2026-01-01", "recipe_id": 1, "recipe_name": "A", "servings": 2},
                        {"date": "2026-01-02", "recipe_id": 1, "recipe_name": "A", "servings": 2},
                    ],
                },
            )
        data = resp.get_json()
        self.assertEqual(len(data["meals"]), 2)
        shopping_calls = [c for c in calls if c[1].endswith("/shopping/")]
        self.assertEqual(len(shopping_calls), 1)  # same recipe both days -> only added once

    def test_missing_slots_is_a_400(self):
        resp = self.client.post(
            "/api/plan/confirm", json={"tandoor_url": "http://x", "api_token": "tok", "slots": []}
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
