"""Nibble, the family recipe box, as a plugin for bee-database.

- Project `recipes` with one collection `recipes` (schema and guide in recipe.py). People get
  a role in the project like any other: viewer, editor or manager.
- recipe_* tools: a friendlier interface over the generic records (one call to save a whole
  recipe, summaries for the list), also used by the web page and the Claude artifact.
- A fetch_page adapter for Gousto, and recipe_fetch_page, which picks the schema.org recipe
  out of a fetched page.
- The web app at /recipes (or its own hostname, NIBBLE_URL; RECIPES_URL still works).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import webfetch
from records import fts_fallback

from . import gousto
from . import recipe as R

HERE = Path(__file__).parent

LOGIN_HTML = """<!doctype html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Nibble</title>
<style>
 :root{{--bg:#f6f3ea;--card:#fffdf7;--fg:#1d2a33;--muted:#5b6670;--accent:#2c6487;--line:#dcd6c6;--sky:#e3edf2;--err:#b23a2e}}
 @media (prefers-color-scheme:dark){{:root{{--bg:#0f1820;--card:#16222c;--fg:#e9eef2;--muted:#9fb0bd;--accent:#8cc0e0;--line:#2e3f4d;--sky:#13212c;--err:#f0948a}}}}
 body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}}
 .card{{background:var(--card);padding:28px 24px;border-radius:20px;width:min(360px,calc(100vw - 32px));box-sizing:border-box;box-shadow:0 6px 24px #0002}}
 h1{{font:700 1.5rem/1.2 Georgia,serif;margin:0 0 4px}} p{{color:var(--muted);margin:0 0 18px}}
 input{{width:100%;box-sizing:border-box;padding:.7rem .8rem;border:1px solid var(--line);border-radius:10px;font-size:1rem;background:var(--bg);color:var(--fg)}}
 button{{margin-top:12px;width:100%;padding:.75rem;border:0;border-radius:999px;background:var(--accent);color:#fff;font-weight:700;font-size:1rem;cursor:pointer}}
 @media (prefers-color-scheme:dark){{button{{color:#0b1720}}}}
 .err{{color:var(--err)}}
 .google{{display:flex;gap:.6rem;align-items:center;justify-content:center;margin-top:12px;padding:.7rem;border-radius:999px;
   background:#fff;color:#1f1f1f;border:1px solid #747775;font-weight:600;text-decoration:none}}
 .google svg{{width:18px;height:18px}}
 details{{margin-top:16px;color:var(--muted);font-size:.9rem}} summary{{cursor:pointer}}
</style></head><body>
<div class="card">
 <h1>Nibble</h1>
 <p>The family recipe box.</p>
 {error}
 {body}
</div></body></html>"""

PATCHABLE = {"title", "category", "status", "made", "difficulty", "servings", "total_minutes", "source_url",
             "image_url", "description", "notes", "rating", "tags", "extra"}


def setup(ctx) -> None:
    store = ctx.store
    P, C = R.PROJECT, R.COLLECTION

    # This code used to be the in-tree "recipes" plugin of bee-database: take over its project and
    # collection (data, ids and members are untouched; only the owner label changes).
    for table in ("projects", "collections"):
        ctx.db.execute(f"UPDATE {table} SET plugin = ? WHERE plugin = 'recipes'", (ctx.name,))
    ctx.db.commit()

    ctx.project(P, name="Family recipes", description="The family recipe box.", guide=R.PROJECT_GUIDE)
    ctx.collection(P, C, description="One recipe per record, in the family standard format.",
                   guide="Use recipe_save rather than store; it takes the whole recipe in one object.",
                   schema=R.SCHEMA, search_fields=R.SEARCH_FIELDS)
    ctx.before_write(P, C, R.before_write)
    ctx.fetch_adapter(gousto.matches, gousto.fetch)
    ctx.instructions("Family recipes are the `recipes` project: use the recipe_* tools for them "
                     "(recipe_format_guide first).")

    def get_recipe_record(rid: str) -> dict[str, Any]:
        try:
            rec = store.get(ctx.current(), rid)
        except LookupError:
            rec = None
        if not rec or rec["project"] != P or rec["collection"] != C:
            raise LookupError(f"No recipe with id {rid}")
        return rec

    @ctx.tool(P, "read")
    def recipe_format_guide() -> dict[str, Any]:
        """The family recipe standard format and the JSON shape recipe_save expects.
        Read this before writing or converting a recipe."""
        return {"guide": R.FORMAT_GUIDE, "categories": list(R.CATEGORIES), "icons": list(R.ICONS)}

    @ctx.tool(P, "read")
    def recipe_list(
        category: str | None = None,
        status: str | None = None,
        query: str | None = None,
        tag: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List family recipes (summaries, no steps). category: meals | baking | bread | ice-cream.
        status: draft | live. query: full-text search over titles, tags, ingredients and steps."""
        meq: dict[str, Any] = {}
        if category:
            meq["category"] = R.choice(category, R.CATEGORIES, "category")
        if status:
            meq["status"] = R.choice(status, R.STATUSES, "status")
        tags = [tag.strip().lower()] if tag and tag.strip() else None
        limit = max(1, min(int(limit), 500))
        p = ctx.current()
        if query and query.strip():
            fts = fts_fallback(query)
            rows = store.search(p, fts, project=P, collection=C, tags=tags, metadata_equals=meq,
                                limit=limit, full=True) if fts else []
        else:
            _, rows = store.list(p, project=P, collection=C, tags=tags, metadata_equals=meq, limit=limit,
                                 order="title", full=True)
        results = [R.summary(r) for r in rows]
        return {"count": len(results), "results": results}

    @ctx.tool(P, "read")
    def recipe_get(id: str) -> dict[str, Any]:
        """Fetch one family recipe in full: components, ingredients and steps."""
        return R.to_recipe(get_recipe_record(id))

    @ctx.tool(P, "write")
    def recipe_save(recipe: dict[str, Any]) -> dict[str, Any]:
        """Create a family recipe (omit id) or replace one entirely (include id).
        Follow recipe_format_guide: components, one action per step, timers on steps.
        New recipes default to status "draft" until someone approves them."""
        if not isinstance(recipe, dict):
            raise ValueError("recipe must be an object")
        p = ctx.current()
        meta = R.to_metadata(recipe)
        rid = R._str(recipe.get("id"))
        if rid:
            cur = get_recipe_record(rid)
            if not meta.get("origin"):
                meta["origin"] = cur["metadata"].get("origin")
            rec = store.update(p, rid, title=str(recipe.get("title") or ""), tags=recipe.get("tags") or [],
                               metadata=meta, replace_metadata=True)
        else:
            rec = store.create(p, project=P, collection=C, title=str(recipe.get("title") or ""),
                               tags=recipe.get("tags") or [], metadata=meta)
        return R.to_recipe(rec)

    @ctx.tool(P, "write")
    def recipe_patch(id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Change top-level fields of a recipe (status, made, rating, notes, tags, servings...)
        without resending its components."""
        if not isinstance(fields, dict):
            raise ValueError("fields must be an object")
        bad = set(fields) - PATCHABLE
        if bad:
            raise ValueError(f"Cannot patch {', '.join(sorted(bad))}; use recipe_save to change the body")
        cur = recipe_get(id)
        cur.update(fields)
        return recipe_save(cur)

    @ctx.tool(P, "delete")
    def recipe_delete(id: str) -> dict[str, Any]:
        """Permanently delete a family recipe. Confirm with the user first."""
        get_recipe_record(id)
        return store.delete(ctx.current(), id)

    @ctx.tool(P, "write", blocking=True)
    def recipe_fetch_page(url: str) -> dict[str, Any]:
        """Fetch a public recipe web page for importing. Returns the page's schema.org recipe data
        when it has some (ingredients, times, original instructions), the readable page text, and
        the format guide. Convert it to the standard format yourself, then call recipe_save."""
        try:
            page = webfetch.fetch_page(url)
        except ValueError:
            raise
        except Exception as e:  # network errors, HTTP errors, timeouts
            raise ValueError(f"Could not fetch that page: {e}") from e
        found = R.find_recipe(page.get("jsonld") or [])
        structured = R.structured_from_jsonld(found) if found else None
        out = {k: v for k, v in page.items() if k != "jsonld"}
        out["structured"] = structured
        out["image"] = (structured or {}).get("image") or page.get("image")
        out["format_guide"] = R.FORMAT_GUIDE
        return out

    ctx.web_app(
        path="/recipes", project=P, page=HERE / "nibble.html", title="Nibble",
        blurb="The family recipe box.", host_url=(os.environ.get("NIBBLE_URL") or os.environ.get("RECIPES_URL", "")).rstrip("/"),
        login_template=LOGIN_HTML, password_label="Open the recipe box",
    )
