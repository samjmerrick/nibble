// Nibble: the family recipe app as a bee app. Serves the page at /nibble and a JSON API at /nibble/api/<tool>.
// Recipes are records in the `nibble` project of the shared core database, reached through the bee gateway.
import { app } from "bee-kit";
import * as R from "./recipe.js";
import { UserError } from "./recipe.js";
import * as llm from "./llm.js";
import * as textparse from "./textparse.js";
import * as webfetch from "./webfetch.js";
import * as gousto from "./gousto.js";

webfetch.addAdapter("gousto", gousto.matches, gousto.fetchGousto);
const P = R.PROJECT, C = R.COLLECTION;
const LEVEL = { viewer: 1, editor: 2, manager: 3, admin: 9 };
const NEED = { read: 1, write: 2, delete: 3 };
const HEADERS = { "X-Frame-Options": "DENY", "Cache-Control": "no-store", "Referrer-Policy": "same-origin" };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
const err = (code, message, status) => json({ error: { code, message } }, status);
class NotFound extends Error {}

function tools(env, bee) {
  const allRecipes = async (filters = {}) => {
    const out = [];
    for (let offset = 0; ; offset += 200) {
      const page = await bee.call("list_records", { project: P, collection: C, include_metadata: true, limit: 200, offset, ...filters });
      out.push(...page.results);
      if (offset + 200 >= page.total || !page.results.length) return out;
    }
  };
  const getRecord = async (id) => {
    let rec;
    try { rec = await bee.call("get", { id }); } catch { throw new NotFound(`No recipe with id ${id}`); }
    if (!rec || rec.project !== P || rec.collection !== C) throw new NotFound(`No recipe with id ${id}`);
    return rec;
  };
  const imported = (rec, by, why) => ({ ...rec, _import: { by, ...(why ? { why } : {}) } });

  const recipe_format_guide = async () => ({ guide: R.FORMAT_GUIDE, categories: R.CATEGORIES, icons: R.ICONS });
  const recipe_list = async ({ category, status, query, tag, limit = 200 } = {}) => {
    const meq = {};
    if (category) meq.category = R.choice(category, R.CATEGORIES, "category");
    if (status) meq.status = R.choice(status, R.STATUSES, "status");
    const tags = tag && String(tag).trim() ? [String(tag).trim().toLowerCase()] : null;
    let rows = await allRecipes({ ...(Object.keys(meq).length ? { metadata_equals: meq } : {}), ...(tags ? { tags } : {}) });
    if (query && String(query).trim()) {
      const fts = R.ftsFallback(query);
      const hits = fts ? await bee.call("search", { query: fts, project: P, collection: C, limit: 100 }) : { results: [] };
      const ids = new Set(hits.results.map((h) => h.id));
      rows = rows.filter((r) => ids.has(r.id));
    }
    rows.sort((a, b) => a.title.toLowerCase().localeCompare(b.title.toLowerCase()));
    const results = rows.slice(0, Math.max(1, Math.min(Number(limit), 500))).map(R.summary);
    return { count: results.length, results };
  };
  const recipe_get = async ({ id }) => R.toRecipe(await getRecord(id));
  const recipe_save = async ({ recipe }) => {
    if (!recipe || typeof recipe !== "object" || Array.isArray(recipe)) throw new UserError("recipe must be an object");
    const meta = R.toMetadata(recipe);
    const rid = R.str(recipe.id);
    if (rid) { const cur = await getRecord(rid); if (!meta.origin) meta.origin = cur.metadata.origin ?? null; }
    const rec = R.beforeWrite({ title: String(recipe.title ?? ""), tags: recipe.tags ?? [], metadata: meta });
    const saved = rid
      ? await bee.call("update", { id: rid, title: rec.title, tags: rec.tags, content: rec.content, metadata: rec.metadata, replace_metadata: true })
      : await bee.call("store", { project: P, collection: C, title: rec.title, tags: rec.tags, content: rec.content, metadata: rec.metadata });
    return R.toRecipe(saved);
  };
  const recipe_patch = async ({ id, fields }) => {
    if (!fields || typeof fields !== "object" || Array.isArray(fields)) throw new UserError("fields must be an object");
    const bad = Object.keys(fields).filter((k) => !R.PATCHABLE.has(k));
    if (bad.length) throw new UserError(`Cannot patch ${bad.sort().join(", ")}; use recipe_save to change the body`);
    return recipe_save({ recipe: { ...(await recipe_get({ id })), ...fields } });
  };
  const recipe_delete = async ({ id }) => { await getRecord(id); return bee.call("delete", { id }); };
  const recipe_fetch_page = async ({ url }) => {
    let page;
    try { page = await webfetch.fetchPage(url); }
    catch (e) { if (e instanceof UserError) throw e; throw new UserError(`Could not fetch that page: ${e.message}`); }
    const found = R.findRecipe(page.jsonld ?? []);
    const structured = found ? R.structuredFromJsonld(found) : null;
    const { jsonld, ...rest } = page;
    return { ...rest, structured, image: structured?.image || page.image, format_guide: R.FORMAT_GUIDE };
  };
  const recipe_parse_text = async ({ text, source_url = null }) => {
    if (typeof text !== "string" || !text.trim()) throw new UserError("Paste the recipe text first.");
    text = text.slice(0, 40000);
    let why = null;
    if (llm.enabled(env)) {
      try {
        const [rec, model] = await llm.convert(env, "Recipe text", text);
        if (source_url && !rec.source_url) rec.source_url = source_url;
        return imported(rec, model);
      } catch (e) { if (!(e instanceof llm.LlmError)) throw e; why = e.message; }
    }
    return imported(textparse.parseText(text, source_url), "reader", why);
  };
  const fromStructured = (st, page) => ({
    title: st.name || page.title || "Untitled recipe", category: "meals", status: "draft", description: st.description || "",
    servings: R.int((String(st.yield ?? "").match(/\d+/) ?? [null])[0]), total_minutes: st.total_minutes ?? null,
    extra: st.cuisine ? { cuisine: String(st.cuisine) } : {},
    notes: "Imported as written on the source page; steps not yet split into single actions.",
    components: [{ name: "", ingredients: (st.ingredients ?? []).map((t) => textparse.parseIngredient(String(t))), steps: (st.instructions ?? []).map((x) => ({ text: x.text })) }],
  });
  const recipe_import_page = async ({ url }) => {
    const page = await recipe_fetch_page({ url });
    const st = page.structured;
    const base = { source_url: page.url || url, image_url: page.image };
    let why = null;
    if (llm.enabled(env)) {
      const payload = JSON.stringify({ structured_data: st, page_text: (page.text ?? "").slice(0, st ? 6000 : 15000), url: page.url });
      try {
        const [rec, model] = await llm.convert(env, "Source page (structured_data is exact; use it for ingredients and quantities)", payload);
        const fill = Object.fromEntries(Object.entries(base).filter(([k, v]) => v && !rec[k]));
        return imported({ ...rec, ...fill }, model);
      } catch (e) { if (!(e instanceof llm.LlmError)) throw e; why = e.message; }
    }
    if (!st || !(st.ingredients ?? []).length) throw new UserError("That page has no recipe data Nibble can read. Paste the recipe text instead.");
    return imported({ ...fromStructured(st, page), ...Object.fromEntries(Object.entries(base).filter(([, v]) => v)) }, "page", why);
  };
  return {
    recipe_format_guide: ["read", recipe_format_guide], recipe_list: ["read", recipe_list], recipe_get: ["read", recipe_get],
    recipe_save: ["write", recipe_save], recipe_patch: ["write", recipe_patch], recipe_delete: ["delete", recipe_delete],
    recipe_fetch_page: ["write", recipe_fetch_page], recipe_parse_text: ["write", recipe_parse_text], recipe_import_page: ["write", recipe_import_page],
  };
}

export default app("nibble", {
  async fetch(request, env, ctx, bee) {
    const url = new URL(request.url);
    const level = LEVEL[bee.user.role] ?? 0;
    const can = (need) => level >= NEED[need];
    const path = url.pathname;

    if (path === "/nibble" || path === "/nibble/") {
      if (!can("read")) return new Response(`<!doctype html><meta charset=utf-8><title>Nibble</title><p>${bee.user.email} hasn't been given access to Nibble. Ask Sam to add you.</p>`, { status: 403, headers: { ...HEADERS, "Content-Type": "text/html; charset=utf-8" } });
      const boot = JSON.stringify({ web: true, user: bee.user.email, project: P, role: bee.user.role, canEdit: can("write"), canDelete: can("delete"), apiBase: "/nibble/api/" }).replaceAll("<", "\\u003c");
      const page = await (await env.ASSETS.fetch(new Request(new URL("/nibble.html", url)))).text();
      return new Response(page.replace("<!--WEB-BOOT-->", `<script>window.NIBBLE_APP=${boot};</script>`), { headers: { ...HEADERS, "Content-Type": "text/html; charset=utf-8" } });
    }
    if (path === "/nibble/logout" && request.method === "POST") return Response.redirect(`${url.origin}/cdn-cgi/access/logout`, 303);

    const m = /^\/nibble\/api\/([a-z_]+)$/.exec(path);
    if (m && request.method === "POST") {
      if (!can("read")) return err("needs_reauth", "Sign in again.", 401);
      if (!(request.headers.get("content-type") ?? "").startsWith("application/json")) return err("bad_request", "JSON only.", 415);
      const origin = request.headers.get("origin");
      if (origin && origin.replace(/\/$/, "") !== url.origin) return err("forbidden", "Wrong origin.", 403);
      let args;
      try { args = await request.json(); if (!args || typeof args !== "object" || Array.isArray(args)) throw 0; } catch { return err("bad_request", "Body must be a JSON object.", 400); }
      const spec = tools(env, bee)[m[1]];
      if (!spec || !can(spec[0])) return err("tool_error", `Your account can't use ${m[1]} here.`, 403);
      try { return json(await spec[1](args)); }
      catch (e) {
        if (e instanceof NotFound) return err("bad_request", e.message.slice(0, 300), 404);
        if (e instanceof UserError || e instanceof llm.LlmError) return err("tool_error", e.message.slice(0, 300), 400);
        console.error("nibble error", e?.message);
        return err("tool_error", String(e?.message ?? e).slice(0, 300), 502);
      }
    }
    return null; // not ours: fall through to static files
  },
});
