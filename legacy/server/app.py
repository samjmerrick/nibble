"""Nibble: the family recipe app, a standalone web service in front of bee-database.

It serves the web page at /nibble and a small JSON API at /nibble/api/<tool>. Recipes are records
in the database's `nibble` project, read and written over its MCP endpoint with a service token.
People and roles are the database's: sign-in happens at the database (/auth/web/login), and each
request asks GET /auth/whoami (forwarding the browser's cookie) who is signed in.

Settings (environment):
  DB_MCP_URL       the database's MCP endpoint (default http://mcp-db:8000/mcp/db)
  DB_URL           the database's base URL for /auth/whoami (default http://mcp-db:8000)
  DB_TOKEN         bearer token for the database (its MCP_API_TOKEN, or another admin token)
  PUBLIC_URL       the address people use, e.g. https://home.samjmerrick.com (for the Origin check)
  PROJECT          the database project holding recipes (default "nibble")
  PORT             listen port (default 8000)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx2 as httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import gousto
import recipe as R
import llm
import textparse
import webfetch
from dbclient import Db, DbError

log = logging.getLogger("nibble")
HERE = Path(__file__).parent
PREFIX = "/nibble"
DB_URL = os.environ.get("DB_URL", "http://mcp-db:8000").rstrip("/")
DB_MCP_URL = os.environ.get("DB_MCP_URL", DB_URL + "/mcp/db")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
P = os.environ.get("PROJECT", R.PROJECT)
C = R.COLLECTION
ROLE_LEVEL = {"viewer": 1, "editor": 2, "manager": 3, "admin": 9}
NEED = {"read": 1, "write": 2, "delete": 3}
PATCHABLE = {"title", "category", "status", "made", "difficulty", "servings", "total_minutes", "source_url",
             "image_url", "description", "notes", "rating", "tags", "extra"}
SECURITY_HEADERS = {"X-Frame-Options": "DENY", "Cache-Control": "no-store", "Referrer-Policy": "same-origin"}

db = Db(DB_MCP_URL, os.environ.get("DB_TOKEN", ""))
webfetch.add_adapter("gousto", gousto.matches, gousto.fetch)


class Forbidden(Exception):
    pass


# ------------------------------------------------------------------ who is signed in


class Person:
    def __init__(self, info: dict[str, Any]) -> None:
        self.email = info.get("email") or ""
        self.name = info.get("name") or ""
        self.admin = bool(info.get("admin"))
        self.role = "admin" if self.admin else (info.get("roles") or {}).get(P)

    def can(self, need: str) -> bool:
        return ROLE_LEVEL.get(self.role or "", 0) >= NEED[need]


async def whoami(request: Request) -> Person | None:
    cookie = request.headers.get("cookie")
    if not cookie:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as hc:
            r = await hc.get(DB_URL + "/auth/whoami", headers={"Cookie": cookie})
    except Exception as e:
        log.warning("whoami failed: %s", e)
        raise DbError("The database didn't answer.") from e
    if r.status_code != 200:
        return None
    return Person(r.json())


# ------------------------------------------------------------------ recipes in the database


async def all_recipes(**filters: Any) -> list[dict[str, Any]]:
    out, offset = [], 0
    while True:
        page = await db.call("list_records", project=P, collection=C, include_metadata=True, limit=200,
                             offset=offset, **filters)
        out += page["results"]
        offset += 200
        if offset >= page["total"] or not page["results"]:
            return out


async def get_record(rid: str) -> dict[str, Any]:
    try:
        rec = await db.call("get", id=rid)
    except DbError as e:
        raise LookupError(f"No recipe with id {rid}") from e
    if not isinstance(rec, dict) or rec.get("project") != P or rec.get("collection") != C:
        raise LookupError(f"No recipe with id {rid}")
    return rec


async def recipe_format_guide() -> dict[str, Any]:
    return {"guide": R.FORMAT_GUIDE, "categories": list(R.CATEGORIES), "icons": list(R.ICONS)}


async def recipe_list(category: str | None = None, status: str | None = None, query: str | None = None,
                      tag: str | None = None, limit: int = 200) -> dict[str, Any]:
    meq: dict[str, Any] = {}
    if category:
        meq["category"] = R.choice(category, R.CATEGORIES, "category")
    if status:
        meq["status"] = R.choice(status, R.STATUSES, "status")
    tags = [tag.strip().lower()] if tag and tag.strip() else None
    rows = await all_recipes(metadata_equals=meq or None, tags=tags)
    if query and query.strip():
        fts = R.fts_fallback(query)
        hits = await db.call("search", query=fts, project=P, collection=C, limit=100) if fts else {"results": []}
        ids = {h["id"] for h in hits["results"]}
        rows = [r for r in rows if r["id"] in ids]
    rows.sort(key=lambda r: r["title"].lower())
    results = [R.summary(r) for r in rows[: max(1, min(int(limit), 500))]]
    return {"count": len(results), "results": results}


async def recipe_get(id: str) -> dict[str, Any]:
    return R.to_recipe(await get_record(id))


async def recipe_save(recipe: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(recipe, dict):
        raise ValueError("recipe must be an object")
    meta = R.to_metadata(recipe)
    rid = R._str(recipe.get("id"))
    if rid:
        cur = await get_record(rid)
        if not meta.get("origin"):
            meta["origin"] = cur["metadata"].get("origin")
    rec = R.before_write({"title": str(recipe.get("title") or ""), "tags": recipe.get("tags") or [], "metadata": meta})
    if rid:
        saved = await db.call("update", id=rid, title=rec["title"], tags=rec["tags"], content=rec["content"],
                              metadata=rec["metadata"], replace_metadata=True)
    else:
        saved = await db.call("store", project=P, collection=C, title=rec["title"], tags=rec["tags"],
                              content=rec["content"], metadata=rec["metadata"])
    return R.to_recipe(saved)


async def recipe_patch(id: str, fields: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    bad = set(fields) - PATCHABLE
    if bad:
        raise ValueError(f"Cannot patch {', '.join(sorted(bad))}; use recipe_save to change the body")
    cur = await recipe_get(id)
    cur.update(fields)
    return await recipe_save(cur)


async def recipe_delete(id: str) -> dict[str, Any]:
    await get_record(id)
    return await db.call("delete", id=id)


async def recipe_fetch_page(url: str) -> dict[str, Any]:
    try:
        page = await asyncio.to_thread(webfetch.fetch_page, url)
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


def _imported(rec: dict[str, Any], by: str, why: str | None = None) -> dict[str, Any]:
    """Tell the page how the draft was made (a model, or Nibble's own reader and why)."""
    rec["_import"] = {"by": by, **({"why": why} if why else {})}
    return rec


async def recipe_parse_text(text: str, source_url: str | None = None) -> dict[str, Any]:
    """Pasted recipe text -> a draft recipe: by the model when one is set up, else Nibble's own reader."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Paste the recipe text first.")
    text = text[:40000]
    why = None
    if llm.enabled():
        try:
            rec, model = await llm.convert("Recipe text", text)
            if source_url and not rec.get("source_url"):
                rec["source_url"] = source_url
            return _imported(rec, model)
        except llm.LlmError as e:
            why = str(e)
            log.warning("model import failed, using the reader: %s", e)
    return _imported(textparse.parse_text(text, source_url), "reader", why)


def _from_structured(st: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    """A page's schema.org recipe as a draft, steps as the page wrote them."""
    return {
        "title": st.get("name") or page.get("title") or "Untitled recipe", "category": "meals", "status": "draft",
        "description": st.get("description") or "", "servings": R._int((re.findall(r"\d+", str(st.get("yield") or "")) or [None])[0]),
        "total_minutes": st.get("total_minutes"), "extra": {"cuisine": str(st["cuisine"])} if st.get("cuisine") else {},
        "notes": "Imported as written on the source page; steps not yet split into single actions.",
        "components": [{"name": "", "ingredients": [textparse.parse_ingredient(str(t)) for t in st.get("ingredients") or []],
                        "steps": [{"text": x["text"]} for x in st.get("instructions") or []]}],
    }


async def recipe_import_page(url: str) -> dict[str, Any]:
    """A recipe web page -> a draft recipe: the model when set up, else the page's own recipe data."""
    page = await recipe_fetch_page(url)
    st = page.get("structured")
    base = {"source_url": page.get("url") or url, "image_url": page.get("image")}
    why = None
    if llm.enabled():
        payload = json.dumps({"structured_data": st, "page_text": (page.get("text") or "")[:6000 if st else 15000],
                              "url": page.get("url")})
        try:
            rec, model = await llm.convert("Source page (structured_data is exact; use it for ingredients and quantities)", payload)
            return _imported({**rec, **{k: v for k, v in base.items() if v and not rec.get(k)}}, model)
        except llm.LlmError as e:
            why = str(e)
            log.warning("model import failed, using the page data: %s", e)
    if not st or not st.get("ingredients"):
        raise ValueError("That page has no recipe data Nibble can read. Paste the recipe text instead.")
    return _imported({**_from_structured(st, page), **{k: v for k, v in base.items() if v}}, "page", why)


TOOLS = {
    "recipe_format_guide": ("read", recipe_format_guide), "recipe_list": ("read", recipe_list),
    "recipe_get": ("read", recipe_get), "recipe_save": ("write", recipe_save),
    "recipe_patch": ("write", recipe_patch), "recipe_delete": ("delete", recipe_delete),
    "recipe_fetch_page": ("write", recipe_fetch_page), "recipe_parse_text": ("write", recipe_parse_text),
    "recipe_import_page": ("write", recipe_import_page),
}


async def ensure_schema() -> None:
    """Define the recipes project and collection in the database (idempotent)."""
    for attempt in range(30):
        try:
            projects = await db.call("list_projects")
            known = {p["project"] for p in (projects.get("projects") if isinstance(projects, dict) else projects) or []}
            await db.call("project_set", project=P, name=None if P in known else "Nibble",
                          description="Nibble, the family recipe app.", guide=R.PROJECT_GUIDE)
            await db.call("collection_set", project=P, collection=C,
                          description="One recipe per record, in the family standard format.",
                          guide="Nibble writes these; use the recipes app (or keep to the format guide) when editing.",
                          schema=R.SCHEMA, search_fields=R.SEARCH_FIELDS)
            log.info("recipes project is set up")
            return
        except DbError as e:
            log.warning("database not ready (%s); retrying", e)
            await asyncio.sleep(min(2 * (attempt + 1), 20))
    log.error("could not set up the recipes project; Nibble will run without it")


# ------------------------------------------------------------------ web


def err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def login_redirect(path: str = PREFIX) -> Response:
    return RedirectResponse(f"/auth/web/login?next={path}", status_code=303)


async def page(request: Request) -> Response:
    try:
        p = await whoami(request)
    except DbError as e:
        return HTMLResponse(f"<p>{e} Try again in a minute.</p>", status_code=502, headers=SECURITY_HEADERS)
    if not p:
        return login_redirect()
    if not p.can("read"):
        return HTMLResponse(
            f"<!doctype html><meta charset=utf-8><title>Nibble</title><p>{p.email or 'This account'} hasn't been "
            "given access to Nibble. Ask Sam to add you.</p>", status_code=403, headers=SECURITY_HEADERS)
    boot = json.dumps({"web": True, "user": p.email, "project": P, "role": p.role, "canEdit": p.can("write"),
                       "canDelete": p.can("delete"), "apiBase": f"{PREFIX}/api/"}).replace("<", "\\u003c")
    doc = (HERE / "static" / "nibble.html").read_text(encoding="utf-8")
    doc = doc.replace("<!--WEB-BOOT-->", f"<script>window.NIBBLE_APP={boot};</script>", 1)
    return HTMLResponse(doc, headers=SECURITY_HEADERS)


async def api(request: Request) -> Response:
    try:
        p = await whoami(request)
    except DbError as e:
        return err("server_unavailable", str(e), 502)
    if not p or not p.can("read"):
        return err("needs_reauth", "Sign in again.", 401)
    if not request.headers.get("content-type", "").startswith("application/json"):
        return err("bad_request", "JSON only.", 415)
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != PUBLIC_URL:
        return err("forbidden", "Wrong origin.", 403)
    try:
        args = await request.json()
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return err("bad_request", "Body must be a JSON object.", 400)
    name = request.path_params["tool"]
    spec = TOOLS.get(name)
    if not spec or not p.can(spec[0]):
        return err("tool_error", f"Your account can't use {name} here.", 403)
    try:
        out = await spec[1](**args)
    except LookupError as e:
        return err("bad_request", str(e)[:300], 404)
    except TypeError as e:
        return err("bad_request", f"Bad arguments: {e}"[:300], 400)
    except ValueError as e:
        return err("tool_error", str(e)[:300], 400)
    except DbError as e:
        return err("tool_error", str(e)[:300], 502)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


async def logout(request: Request) -> Response:
    # The database owns the session; 307 repeats this POST there.
    return RedirectResponse("/auth/web/logout", status_code=307)


async def health(request: Request) -> Response:
    return JSONResponse({"ok": True})


@contextlib.asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(ensure_schema())
    yield
    task.cancel()


app = Starlette(
    routes=[
        Route(PREFIX, page), Route(PREFIX + "/", page),
        Route(f"{PREFIX}/api/{{tool}}", api, methods=["POST"]),
        Route(f"{PREFIX}/logout", logout, methods=["POST"]),
        Route(f"{PREFIX}/health", health), Route("/health", health),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=True, forwarded_allow_ips="*")
