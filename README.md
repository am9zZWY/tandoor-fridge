# Fridge Planner for Tandoor

Take a photo of your fridge, get a meal plan built from recipes you can actually
make, and a shopping list of whatever's missing — using your existing Tandoor
recipe collection.

It's a single small Flask app (`fridgeplan/`) that talks to Tandoor's REST API.
It doesn't modify Tandoor itself.

## How it works

1. You upload a fridge photo and give it your Tandoor URL + API token, and pick
   a vision provider (Anthropic, OpenAI, or Mistral).
2. It fetches your Tandoor food database and asks the chosen provider's vision
   model which of your *existing* foods are visible in the photo.
3. It marks those foods `food_onhand = true` in Tandoor.
4. It picks recipes using Tandoor's own `makenow` filter (recipes you can
   already make), falling back to whichever recipes have the highest fraction
   of on-hand ingredients if there aren't enough exact matches.
5. It creates meal plan entries for the days/meals you asked for.
6. For each recipe added, it calls Tandoor's own
   `PUT /api/recipe/{id}/shopping/`, which adds the recipe's ingredients to
   your shopping list — automatically skipping anything already on hand.

Nothing here reimplements matching or shopping-list logic; steps 4 and 6 use
features Tandoor already has.

## Prerequisites

- Docker + Docker Compose
- An API key for at least one vision-capable LLM provider: Anthropic, OpenAI,
  or Mistral (for the fridge-photo matching step)
- A Tandoor instance with a **read-write** API token (see below)

## Setup

Add whichever provider key(s) you want to use to `.env` — you only need the
one(s) you'll actually select in the UI:

```bash
cd tandoor-ai-photo
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
echo "OPENAI_API_KEY=sk-..." >> .env
echo "MISTRAL_API_KEY=..." >> .env
docker compose up -d
```

Each provider also has an optional `_MODEL` override (`ANTHROPIC_MODEL`,
`OPENAI_MODEL`, `MISTRAL_MODEL`) if you want a specific model instead of the
built-in default.

This starts three containers:

| Service      | URL                     | Purpose                              |
|--------------|-------------------------|---------------------------------------|
| `tandoor`    | http://localhost:8080   | A local Tandoor, for testing          |
| `fridgeplan` | http://localhost:5050   | The fridge-planner web page           |
| `db`         | (internal)              | Postgres for the local Tandoor        |

The local Tandoor already has an admin account: **username `admin`, password
`adminpass123`**. Log in there if you want to add your own recipes to test
with, or just use it as a throwaway sandbox.

You do **not** need the local Tandoor at all if you only want to point the
tool at your real, already-running Tandoor instance — it's there for testing
and for trying the tool safely before using it on your real recipe data.

## Getting a Tandoor API token

Open your Tandoor instance → **Settings → API** → generate a new token.

Use a token generated this way, not a manually copy-pasted read-only one —
this tool needs to write (mark food on-hand, create meal plan entries, add to
the shopping list). A read-only token will get a `403 Forbidden` on the first
write and stop there.

## Using it

1. Open http://localhost:5050
2. Fill in:
   - **Tandoor URL** — e.g. `https://recipes.example.com`, or
     `http://localhost:8080` for the bundled local instance
   - **API Token** — the read-write token from the step above
   - **Vision provider** — Anthropic, OpenAI, or Mistral (whichever key you set
     in `.env`)
   - **Days** / **Meals per day** — how many meal slots to fill
   - **Fridge photo** — a picture of your fridge/pantry contents
3. Click **Generate plan**.

The URL and token are remembered in your browser (`localStorage`) so you don't
have to retype them each time. Nothing is stored server-side.

The result shows which foods were recognized, the meal plan that was created,
and a link to your Tandoor shopping list.

## Limitations

- Only foods that already exist in your Tandoor food database can be detected
  — it matches against your existing data, it doesn't create new food entries.
  If a recipe needs an ingredient you've never added to Tandoor before, teach
  Tandoor about that food first (add it to any recipe once).
- Running the planner again on the same photo will add the same recipes to
  the shopping list again rather than deduplicating — fine for occasional,
  single-user use; not meant for repeated automated runs.
- Recipe picking falls back to scanning up to 100 recipes if `makenow` alone
  doesn't fill all your meal slots. For very large recipe collections this
  fallback gets slower; the exact-match `makenow` path is unaffected.

## Troubleshooting

- **"server is missing ANTHROPIC_API_KEY" / `OPENAI_API_KEY` / `MISTRAL_API_KEY`**
  — set the key for whichever provider you selected in `.env` and
  `docker compose up -d` again.
- **403 from Tandoor** — your token is read-only; generate a new one (see
  above).
- **400 "Invalid HTTP_HOST header"** from a self-hosted Tandoor reached via a
  Docker-internal name — Django rejects hostnames containing underscores.
  Use a service name without underscores, or point at Tandoor's public URL
  instead.
