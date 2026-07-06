import base64
import itertools
import json
import os
import re
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlparse
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


def tandoor(base_url, token, method, path, params=None, body=None):
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw.decode(errors="replace")}


def fetch_all_foods(base_url, token):
    foods = {}
    path, params = "/api/food/", {"page_size": 200}
    while path:
        status, data = tandoor(base_url, token, "GET", path, params=params)
        if status != 200:
            raise RuntimeError(f"could not list foods: {data}")
        for f in data["results"]:
            foods[f["id"]] = f["name"]
        nxt = data.get("next")
        path, params = (urlparse(nxt).path, dict(parse_qsl(urlparse(nxt).query))) if nxt else (None, None)
    return foods


# One entry per supported vision LLM provider. Each is a plain REST call, so no
# provider SDK is needed - just a different request shape and response path.
PROVIDERS = {
    "anthropic": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-sonnet-5",
    },
    "openai": {"api_key_env": "OPENAI_API_KEY", "model_env": "OPENAI_MODEL", "default_model": "gpt-4o-mini"},
    "mistral": {
        "api_key_env": "MISTRAL_API_KEY",
        "model_env": "MISTRAL_MODEL",
        "default_model": "pixtral-12b-2409",
    },
}


def _build_vision_request(provider, model, api_key, b64_image, mime_type, prompt_text):
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        body = {
            "model": model,
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime_type, "data": b64_image},
                        },
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ],
        }
    elif provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}},
                    ],
                }
            ],
            "max_completion_tokens": 1024,
        }
    elif provider == "mistral":
        url = "https://api.mistral.ai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": f"data:{mime_type};base64,{b64_image}"},
                    ],
                }
            ],
        }
    else:
        raise ValueError(f"unknown provider: {provider}")
    return url, headers, body


def _extract_vision_text(provider, result):
    if provider == "anthropic":
        return result["content"][0]["text"]
    return result["choices"][0]["message"]["content"]  # openai and mistral share this shape


def call_vision_llm(provider, image_bytes, mime_type, prompt_text):
    cfg = PROVIDERS[provider]
    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        raise RuntimeError(f"{cfg['api_key_env']} is not set on the server")
    model = os.environ.get(cfg["model_env"], cfg["default_model"])
    b64_image = base64.b64encode(image_bytes).decode()

    url, headers, body = _build_vision_request(provider, model, api_key, b64_image, mime_type, prompt_text)
    req = Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("content-type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    with urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    return _extract_vision_text(provider, result)


def identify_onhand_food_ids(provider, image_bytes, mime_type, foods):
    food_list_text = "\n".join(f"{fid}: {name}" for fid, name in foods.items())
    prompt_text = (
        "Here is a pantry database's known food items as `id: name` pairs:\n\n"
        f"{food_list_text}\n\n"
        "Look at the attached fridge/pantry photo and reply with ONLY a JSON array of the "
        "ids (integers) from the list above that are visibly present in the photo. Only use "
        "ids from the list above, never invent new ones. If nothing matches, reply []."
    )
    text = call_vision_llm(provider, image_bytes, mime_type, prompt_text)
    match = re.search(r"\[.*\]", text, re.S)
    ids = json.loads(match.group(0)) if match else []
    return [int(i) for i in ids if int(i) in foods]


def pick_recipes(base_url, token, slots):
    status, data = tandoor(
        base_url, token, "GET", "/api/recipe/", params={"makenow": "true", "page_size": slots}
    )
    results = data.get("results", []) if status == 200 else []
    if len(results) >= slots:
        return results[:slots]

    # ponytail: scans up to 100 recipes with one detail fetch each (N+1) to rank by
    # fraction of on-hand ingredients. Fine for a personal collection; if this ever
    # needs to scale past ~100 recipes, replace with a server-side ingredient filter.
    status, data = tandoor(base_url, token, "GET", "/api/recipe/", params={"page_size": 100})
    candidates = data.get("results", []) if status == 200 else []
    scored = []
    for r in candidates:
        status, detail = tandoor(base_url, token, "GET", f"/api/recipe/{r['id']}/")
        if status != 200:
            continue
        onhand_flags = [
            ing["food"]["food_onhand"]
            for step in detail.get("steps", [])
            for ing in step.get("ingredients", [])
            if not ing.get("is_header") and ing.get("food")
        ]
        if onhand_flags:
            scored.append((sum(onhand_flags) / len(onhand_flags), r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]


def build_plan(provider, base_url, token, image_bytes, mime_type, days, meals_per_day):
    foods = fetch_all_foods(base_url, token)
    onhand_ids = identify_onhand_food_ids(provider, image_bytes, mime_type, foods)
    for fid in onhand_ids:
        tandoor(base_url, token, "PATCH", f"/api/food/{fid}/", body={"food_onhand": True})

    recipes = pick_recipes(base_url, token, days * meals_per_day)

    status, meal_types = tandoor(base_url, token, "GET", "/api/meal-type/", params={"page_size": 10})
    meal_type_id = meal_types["results"][0]["id"] if meal_types.get("results") else None

    created_meals, shopping_added = [], set()
    recipe_cycle = itertools.cycle(recipes) if recipes else None
    today = date.today()
    for day_offset in range(days):
        d = (today + timedelta(days=day_offset)).isoformat()
        for _ in range(meals_per_day):
            if recipe_cycle is None:
                break
            recipe = next(recipe_cycle)
            servings = recipe.get("servings") or 1
            body = {
                "recipe": recipe["id"],
                "title": "",
                "servings": servings,
                "from_date": d,
                "to_date": d,
                "shared": [],
            }
            if meal_type_id:
                body["meal_type"] = meal_type_id
            status, mp = tandoor(base_url, token, "POST", "/api/meal-plan/", body=body)
            if status == 201:
                created_meals.append({"date": d, "recipe": recipe["name"], "id": mp["id"]})
                if recipe["id"] not in shopping_added:
                    tandoor(
                        base_url,
                        token,
                        "PUT",
                        f"/api/recipe/{recipe['id']}/shopping/",
                        body={"servings": servings},
                    )
                    shopping_added.add(recipe["id"])

    return {
        "onhand_matched": [foods[i] for i in onhand_ids],
        "meals": created_meals,
        "shopping_list_url": base_url.rstrip("/") + "/list",
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/plan")
def api_plan():
    base_url = request.form.get("tandoor_url", "").strip() or os.environ.get("TANDOOR_URL", "").strip()
    token = request.form.get("api_token", "").strip() or os.environ.get("TANDOOR_TOKEN", "").strip()
    provider = request.form.get("provider", "anthropic").strip().lower()
    days = max(1, min(int(request.form.get("days", 3)), 14))
    meals_per_day = max(1, min(int(request.form.get("meals_per_day", 1)), 4))
    image = request.files.get("image")

    if not base_url or not token or not image:
        return jsonify({"error": "tandoor_url, api_token and image are required"}), 400
    if provider not in PROVIDERS:
        return jsonify({"error": f"unknown provider '{provider}', choose one of {list(PROVIDERS)}"}), 400
    if not os.environ.get(PROVIDERS[provider]["api_key_env"]):
        return jsonify({"error": f"server is missing {PROVIDERS[provider]['api_key_env']}"}), 500

    try:
        result = build_plan(
            provider, base_url, token, image.read(), image.mimetype or "image/jpeg", days, meals_per_day
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
