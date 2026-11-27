# Nibble

The family recipe app: one place for the family's recipes, with a cook mode (timers, scaling, step-by-step), draft/live
status, Gousto, web-page and pasted-text import. Claude can also add and edit recipes through the database connector.
Formerly "Moominmamma's Kitchen" / "Family Recipe Box".

Nibble is an app of the Bee platform: a Cloudflare Worker at `https://apps.samjmerrick.com/nibble`, behind Cloudflare
Access (emailed code). It has no database of its own. Recipes are records in the `nibble` project of the shared core
database, reached through the bee gateway (`env.BEE`, see [bee-kit](https://github.com/samjmerrick/bee-kit)), and the
core's roles decide who can see (viewer), edit (editor) and delete (manager).

| Piece | Where |
|---|---|
| Worker: page, JSON API at `/nibble/api/<tool>` | `server/index.js` |
| Recipe format, schema, guide for Claude | `server/recipe.js`, `server/guide.js` |
| Gousto cookbook adapter and safe page fetching | `server/gousto.js`, `server/webfetch.js` |
| Pasted recipe text to a draft (no AI) | `server/textparse.js` |
| AI conversion of imports to the family format | `server/llm.js` |
| The web app (single HTML file) | `public/nibble.html` |
| Manifest: path, project, tools it may call | `app.json` |

## API

The page calls `POST /nibble/api/<tool>` with a JSON body; each call checks the signed-in person's role. Tools:
`recipe_format_guide`, `recipe_list`, `recipe_get` (viewer), `recipe_save`, `recipe_patch`, `recipe_fetch_page`,
`recipe_parse_text`, `recipe_import_page` (editor), `recipe_delete` (manager).

## Imports and AI

"From a link" and "Paste text" are converted into the family format (one action per step, timers, grams) by a language
model when the app's `LLM_API_KEY` secret is set: any OpenAI-compatible endpoint (`LLM_BASE_URL`, `LLM_MODEL`,
`LLM_TIMEOUT`; OpenRouter's free models by default). The answer is checked before it is shown. With no key, or when the
model fails, Nibble falls back to its own reader (pasted text) or the page's recipe data (links), keeping the steps as
written; the review screen says which was used. The settings are per-app secrets kept by the gateway.

## Deploying and developing

Pushing to `main` runs the deploy action from bee-kit (needs the `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`
repository secrets). `npm test` runs the pasted-text reader tests.
