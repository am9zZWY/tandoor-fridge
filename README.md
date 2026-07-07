# Fridge Planner for Tandoor

Photo of your fridge in, a [Tandoor](https://tandoor.dev) meal plan and shopping
list out. A small Flask app (`fridgeplan/`) that talks to Tandoor's REST API —
it doesn't modify Tandoor itself.

It matches your photo against foods already in your Tandoor food database,
marks them on-hand, then uses Tandoor's own `makenow` filter and shopping-list
endpoint to build a plan from what you can already make.

## Quick start

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env   # or OPENAI_API_KEY / MISTRAL_API_KEY
docker compose up -d
```

Open http://localhost:5050. This also starts a throwaway local Tandoor at
http://localhost:8080 (login `admin` / `adminpass123`) for testing — skip it
and point the form at your real Tandoor instance if you already have one.

Fill in your Tandoor URL, a **read-write** API token (Settings → API in
Tandoor — a read-only token will 403 on the first write), pick a vision
provider, and upload a fridge photo. URL/token are remembered in
`localStorage`; nothing is stored server-side.

## Deploying against your real Tandoor

`docker-compose.yml` is the local dev/test stack. To run just the app against
your existing Tandoor, pulling the prebuilt image from GHCR instead of
building from source:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
docker compose -f docker-compose.prod.yml up -d
```

If it's always the same Tandoor instance, also set `TANDOOR_URL` and
`TANDOOR_TOKEN` in `.env` and leave those fields blank in the UI — form values
still win when filled in.

## Development

```bash
cd fridgeplan
uv sync
uv run python -m unittest test_app -v
pre-commit install && pre-commit run --all-files   # ruff lint/format
```

## Limitations

- Only detects foods that already exist in your Tandoor food database.
- Re-running on the same photo re-adds the same recipes to the shopping list
  (no dedup) — fine for occasional single-user use.
- Recipe picking falls back to scanning up to 100 recipes when `makenow`
  doesn't fill all slots, so very large collections make that fallback path
  slower.

## Troubleshooting

- **"server is missing ..._API_KEY"** — set that provider's key in `.env` and
  restart.
- **403 from Tandoor** — token is read-only; generate a read-write one.
- **400 "Invalid HTTP_HOST header"** — Django rejects Docker service names
  with underscores; rename the service or use Tandoor's public URL.
