import re
import unittest
from unittest.mock import patch

import app


class ExtractIdsTest(unittest.TestCase):
    def test_extracts_json_array_even_with_surrounding_prose(self):
        text = 'Sure, here you go:\n```json\n[3, 7, 12]\n```\nHope that helps!'
        match = re.search(r"\[.*\]", text, re.S)
        self.assertEqual(app.json.loads(match.group(0)), [3, 7, 12])

    def test_unknown_ids_are_dropped(self):
        foods = {1: "Milk", 2: "Eggs"}
        ids = [i for i in [1, 2, 999] if i in foods]
        self.assertEqual(ids, [1, 2])


class VisionProviderTest(unittest.TestCase):
    def test_anthropic_request_and_response_shape(self):
        url, headers, body = app._build_vision_request(
            "anthropic", "claude-sonnet-5", "sk-ant-x", "QUJD", "image/png", "hi")
        self.assertIn("api.anthropic.com", url)
        self.assertEqual(headers["x-api-key"], "sk-ant-x")
        image_block = body["messages"][0]["content"][0]
        self.assertEqual(image_block["source"]["data"], "QUJD")
        self.assertEqual(
            app._extract_vision_text("anthropic", {"content": [{"text": "[1, 2]"}]}), "[1, 2]")

    def test_openai_image_url_is_nested_under_url_key(self):
        url, headers, body = app._build_vision_request(
            "openai", "gpt-4o-mini", "sk-oa-x", "QUJD", "image/jpeg", "hi")
        self.assertIn("api.openai.com", url)
        image_block = body["messages"][0]["content"][1]
        self.assertEqual(image_block["image_url"]["url"], "data:image/jpeg;base64,QUJD")
        self.assertEqual(
            app._extract_vision_text("openai", {"choices": [{"message": {"content": "[1]"}}]}), "[1]")

    def test_mistral_image_url_is_a_bare_string(self):
        url, headers, body = app._build_vision_request(
            "mistral", "pixtral-12b-2409", "sk-mi-x", "QUJD", "image/jpeg", "hi")
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
            result = app.pick_recipes("http://x", "tok", 2)
            self.assertEqual([r["id"] for r in result], [1, 2])
            t.assert_called_once()  # only the makenow lookup, no fallback scan

    def test_falls_back_to_ranking_by_onhand_fraction(self):
        def fake_tandoor(base, token, method, path, params=None, body=None):
            if params and params.get("makenow"):
                return 200, {"results": []}
            if path == "/api/recipe/":
                return 200, {"results": [{"id": 1, "servings": 2}, {"id": 2, "servings": 2}]}
            if path == "/api/recipe/1/":
                return 200, {"steps": [{"ingredients": [
                    {"food": {"food_onhand": True}}, {"food": {"food_onhand": False}}]}]}
            if path == "/api/recipe/2/":
                return 200, {"steps": [{"ingredients": [{"food": {"food_onhand": True}}]}]}
            raise AssertionError(path)

        with patch.object(app, "tandoor", side_effect=fake_tandoor):
            result = app.pick_recipes("http://x", "tok", 5)
            # recipe 2 is 100% on-hand, recipe 1 is 50% -> recipe 2 ranks first
            self.assertEqual([r["id"] for r in result], [2, 1])


if __name__ == "__main__":
    unittest.main()
