# Nibble

The family recipe app: one place for the family's recipes, with a cook mode (timers, scaling,
step-by-step), draft/live status, Gousto and web-page import, and Claude can add recipes through the database connector so recipes can
be added and edited by chat. Formerly "Moominmamma's Kitchen" / "Family Recipe Box".

Nibble is its own small web service. It stores everything in
[bee-database](https://github.com/samjmerrick/bee-database) (recipes are records in the `nibble`
project, read and written over the database's MCP endpoint) and signs people in through it:
the database's Google sign-in, people and roles (viewer, editor, manager) decide who can see,
edit and delete. Nibble has no database and no user list of its own.

| Piece | Where |
|---|---|
| HTTP server: page, JSON API, sign-in redirect | `server/app.py` |
| Database client (MCP, service token) | `server/dbclient.py` |
| Recipe format, schema, guide for Claude | `server/recipe.py` |
| Gousto cookbook adapter and safe page fetching | `server/gousto.py`, `server/webfetch.py` |
| The web app (single HTML file) | `server/static/nibble.html` |
| End-to-end tests (real bee-database + Nibble) | `tests/` |

## API

The page calls `POST /nibble/api/<tool>` with a JSON body; each call checks the signed-in
person's role. Tools: `recipe_format_guide`, `recipe_list`, `recipe_get` (viewer), `recipe_save`,
`recipe_patch`, `recipe_fetch_page` (editor), `recipe_delete` (manager).

Claude can also work with recipes directly through the database connector: they are records in
project `nibble` whose collection has the recipe schema and the format guide.

## How sign-in works

A signed-out visitor to `/nibble` is sent to the database's `/auth/web/login?next=/nibble`, then
returns with the shared `bee_session` cookie (same hostname). Nibble asks the database
`GET /auth/whoami` with that cookie to learn who they are and their roles. The database must list
`/nibble` in `SSO_PATHS` (its default).

## Running it

On the server, next to bee-database:

```bash
cd ~/nibble
cp .env.example .env && nano .env      # DB_TOKEN = MCP_API_TOKEN from bee-database/.env
docker compose up -d --build
curl http://127.0.0.1:8766/health
```

It joins bee-database's Docker network (`BEE_NETWORK`, default `bee-database_default`). In the
Cloudflare tunnel, route `home.samjmerrick.com` path `^/nibble(/.*)?$` to `http://nibble:8000`
**above** the rule that sends the rest of the hostname to bee (see bee-database's
`cloudflared-ingress.yml`; in the dashboard it is a Public Hostname with a Path). On start Nibble
defines the `nibble` project and its `recipes` collection in the database (schema and guide) and leaves
existing records and members alone. The old `/recipes` address redirects to `/nibble`.

## Tests

```bash
pip install -r requirements.txt pytest -r ../bee-database/server/requirements.txt
pytest -q tests      # starts ../bee-database/server (or BEE_SERVER_DIR) on a throwaway database
```

## Claude artifact

`/mnt/project-files/recipes-app/index.html` in the family-recipes project is the preview artifact
version of this page (it talks to the connector); the web build here is what the family use.
