# Nibble

The family recipe app: one place for the family's recipes, with a cook mode (timers, scaling,
step-by-step), draft/live status, Gousto and web-page import, and a Claude connector so recipes can
be added and edited by chat. Formerly "Moominmamma's Kitchen" / "Family Recipe Box".

Nibble is a plugin for [bee-database](https://github.com/samjmerrick/bee-database), the personal
MCP database it stores everything in. It has no database of its own: recipes are records in the
`recipes` project, people and sign-in (Google, per-project roles) come from bee.

| Piece | Where |
|---|---|
| Recipe format, schema, guide for Claude | `nibble/recipe.py` |
| `recipe_*` MCP tools, project and collection setup | `nibble/__init__.py` |
| Gousto cookbook adapter for `fetch_page` | `nibble/gousto.py` |
| The web app (single HTML file) | `nibble/nibble.html`, served at `/recipes` |
| Tests | `tests/` |

## Tools

| Tool | Role | What it does |
|---|---|---|
| `recipe_format_guide` | viewer | The standard format and the JSON shape `recipe_save` expects |
| `recipe_list` | viewer | Summaries, filter by category (meals, baking, bread, ice-cream), status, tag or text |
| `recipe_get` | viewer | One recipe in full |
| `recipe_save` | editor | Create (no id) or replace (with id) a whole recipe; new ones start as `draft` |
| `recipe_patch` | editor | Change top-level fields (status, made, rating, notes, tags) without the body |
| `recipe_delete` | manager | Delete a recipe |
| `recipe_fetch_page` | editor | `fetch_page` plus the page's schema.org recipe, ready to convert |

Names, the project slug (`recipes`) and the web path (`/recipes`) are kept from before the split so
the deployed app, bookmarks, connectors and data keep working. Set `NIBBLE_URL=https://nibble.example.com`
(and route that hostname to the container) to serve the app at its own address instead.

## Running it

Bee loads plugins from `PLUGIN_PATH` (default `/plugins`). This repo's `Dockerfile` builds the bee
server together with `nibble/` in that folder, taking the server code from the sibling
`bee-database` checkout:

```
~/bee-database/    server repo, with .env and data/
~/nibble/          this repo
```

```bash
cd ~/nibble
docker compose up -d --build        # replaces the stock mcp-db container: same .env, same ./data
curl http://127.0.0.1:8765/health
```

Updating either repo is `git pull` in it, then the same command in `~/nibble`. Use this compose
file *instead of* the one in bee-database, not alongside it. Override locations with
`BEE_SERVER_DIR`, `BEE_ENV_FILE` and `BEE_DATA_DIR` if yours differ.

On first start with this plugin, bee's project and collection `recipes` (previously owned by the
in-tree `recipes` plugin) are handed to `nibble`. Records, ids, members and sessions are untouched, and
going back to the old bee-database image works too.

## Tests

```bash
pip install -r ../bee-database/server/requirements.txt pytest
pytest -q tests      # uses ../bee-database/server, or set BEE_SERVER_DIR
```

## Claude artifact

`/mnt/project-files/recipes-app/index.html` in the family-recipes project is the preview artifact
version of this page (it talks to the connector); the web build here is what the family use.
