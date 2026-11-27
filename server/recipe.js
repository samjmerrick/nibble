// The family recipe format: guide, schema, normalising, and conversion to and from records.
// A recipe is one record in project `nibble`, collection `recipes`. Title and tags are the record's;
// everything else is in `metadata`. `content` is a readable markdown copy, regenerated on every write.
import { FORMAT_GUIDE } from "./guide.js";

export const PROJECT = "nibble";
export const COLLECTION = "recipes";
export const CATEGORIES = ["meals", "baking", "bread", "ice-cream"];
export const STATUSES = ["draft", "live"];
export const MADE = ["to-try", "made", "favourite"];
export const DIFFICULTIES = ["easy", "medium", "hard"];
export const ICONS = ["bowl", "pot", "pasta", "taco", "chicken", "cake", "loaf-cake", "muffin", "cookie", "bar", "bun", "cheesecake", "loaf", "boule", "ice-cream", "sorbet"];
export { FORMAT_GUIDE };

const TOP_FIELDS = ["category", "status", "made", "difficulty", "servings", "total_minutes", "source_url", "image_url", "description", "notes", "rating", "extra", "origin"];
export const PATCHABLE = new Set(["title", "category", "status", "made", "difficulty", "servings", "total_minutes", "source_url", "image_url", "description", "notes", "rating", "tags", "extra"]);

export class UserError extends Error {}

const tags = (t) => (Array.isArray(t) ? [...new Set(t.map((x) => String(x).trim().toLowerCase()).filter(Boolean))].sort() : []);
export const num = (v) => { if (v === null || v === undefined || v === "") return null; const f = Number(v); return Number.isFinite(f) ? f : null; };
export const int = (v) => { const f = num(v); return f === null ? null : Math.round(f); };
export const str = (v) => { if (v === null || v === undefined) return null; const s = String(v).trim(); return s || null; };
export function choice(v, allowed, field, dflt = null) {
  if (v === null || v === undefined || v === "") return dflt;
  const s = String(v).trim().toLowerCase();
  if (!allowed.includes(s)) throw new UserError(`${field} must be one of ${allowed.join(", ")} (got ${JSON.stringify(v)})`);
  return s;
}
const dropNull = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== undefined));
const isObj = (x) => x !== null && typeof x === "object" && !Array.isArray(x);

export function normalise(recipe) {
  if (!isObj(recipe)) throw new UserError("recipe must be an object");
  const title = str(recipe.title);
  if (!title) throw new UserError("title is required");
  const category = choice(recipe.category, CATEGORIES, "category");
  if (!category) throw new UserError(`category is required: one of ${CATEGORIES.join(", ")}`);
  const comps = recipe.components ?? [];
  if (!Array.isArray(comps)) throw new UserError("components must be a list");
  const components = [];
  for (const c of comps) {
    if (!isObj(c)) throw new UserError("each component must be an object");
    const ings = [], steps = [];
    for (let i of c.ingredients ?? []) {
      if (typeof i === "string") i = { item: i };
      if (!isObj(i)) throw new UserError("each ingredient must be an object or a string");
      const item = str(i.item);
      if (item) ings.push(dropNull({ qty: num(i.qty), qty_max: num(i.qty_max), unit: str(i.unit), item, note: str(i.note) }));
    }
    for (let s of c.steps ?? []) {
      if (typeof s === "string") s = { text: s };
      if (!isObj(s)) throw new UserError("each step must be an object or a string");
      const text = str(s.text);
      if (text) steps.push(dropNull({ text, timer_seconds: int(s.timer_seconds), timer_label: str(s.timer_label), wait_min_minutes: int(s.wait_min_minutes), wait_max_minutes: int(s.wait_max_minutes), cue: str(s.cue), temp_c: num(s.temp_c) }));
    }
    components.push({ name: String(c.name ?? ""), ingredients: ings, steps });
  }
  const meta = {
    category,
    status: choice(recipe.status, STATUSES, "status", "draft"),
    made: choice(recipe.made, MADE, "made", "to-try"),
    difficulty: choice(recipe.difficulty, DIFFICULTIES, "difficulty"),
    servings: int(recipe.servings), total_minutes: int(recipe.total_minutes),
    source_url: str(recipe.source_url), image_url: str(recipe.image_url),
    description: String(recipe.description ?? ""), notes: String(recipe.notes ?? ""),
    rating: num(recipe.rating), extra: isObj(recipe.extra) ? recipe.extra : {}, origin: str(recipe.origin), components,
  };
  return { title, tags: tags(recipe.tags), meta };
}

const fmtNum = (x) => (Number.isInteger(x) ? String(x) : String(Number(x.toPrecision(6))));

export function render(title, meta) {
  const out = [];
  if (meta.description) out.push(meta.description, "");
  const facts = [meta.category];
  if (meta.servings) facts.push(`serves ${meta.servings}`);
  if (meta.total_minutes) facts.push(`${meta.total_minutes} minutes`);
  if (meta.difficulty) facts.push(meta.difficulty);
  facts.push(meta.status);
  out.push(facts.join(" · "), "");
  if (meta.source_url) out.push(`Source: ${meta.source_url}`, "");
  for (const c of meta.components ?? []) {
    out.push(`## ${c.name || "Method"}`);
    if (c.ingredients.length) {
      out.push("");
      for (const i of c.ingredients) {
        let qty = "";
        if ("qty" in i) {
          qty = fmtNum(i.qty) + ("qty_max" in i ? `-${fmtNum(i.qty_max)}` : "");
          qty += i.unit ? " " + i.unit : "";
        }
        const line = [qty, i.item].filter(Boolean).join(" ");
        out.push(`- ${line}` + (i.note ? ` (${i.note})` : ""));
      }
    }
    if (c.steps.length) {
      out.push("");
      c.steps.forEach((s, n) => out.push(`${n + 1}. ${s.text}` + (s.cue ? ` (${s.cue})` : "")));
    }
    out.push("");
  }
  if (meta.notes) out.push("## Notes", "", meta.notes, "");
  return out.join("\n").trim() + "\n";
}

/** Normalise a write to the recipes collection and refresh its content. */
export function beforeWrite(rec) {
  const { title, tags: t, meta } = normalise({ ...(rec.metadata ?? {}), title: rec.title, tags: rec.tags });
  return { ...rec, title, tags: t, metadata: meta, content: render(title, meta) };
}

export function summary(r) {
  const m = r.metadata;
  const extra = Object.fromEntries(Object.entries(m.extra ?? {}).filter(([k]) => k === "cuisine" || k === "icon"));
  return { id: r.id, title: r.title, category: m.category ?? null, status: m.status ?? null, made: m.made ?? null, difficulty: m.difficulty ?? null,
    servings: m.servings ?? null, total_minutes: m.total_minutes ?? null, source_url: m.source_url ?? null, rating: m.rating ?? null,
    tags: r.tags, updated_at: r.updated_at, extra };
}
export function toRecipe(r) {
  const m = r.metadata;
  return { ...summary(r), description: m.description ?? "", notes: m.notes ?? "", image_url: m.image_url ?? null, extra: m.extra ?? {}, origin: m.origin ?? null, created_at: r.created_at, components: m.components ?? [] };
}
export const toMetadata = (recipe) => Object.fromEntries([...TOP_FIELDS, "components"].filter((k) => k in recipe).map((k) => [k, recipe[k]]));

// ---- schema.org import
export function findRecipe(node) {
  if (Array.isArray(node)) { for (const n of node) { const r = findRecipe(n); if (r) return r; } }
  else if (isObj(node)) {
    const t = node["@type"]; const types = Array.isArray(t) ? t : [t];
    if (types.includes("Recipe")) return node;
    for (const k of ["@graph", "mainEntity", "itemListElement"]) if (k in node) { const r = findRecipe(node[k]); if (r) return r; }
  }
  return null;
}
const unescapeHtml = (s) => s.replace(/&(#x?[0-9a-f]+|amp|lt|gt|quot|apos|nbsp);/gi, (m, e) => {
  const t = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " }[e.toLowerCase()];
  if (t) return t;
  const code = e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
  return Number.isFinite(code) ? String.fromCodePoint(code) : m;
});
export { unescapeHtml };
function isoMinutes(v) {
  const m = /^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?$/.exec(String(v ?? "").trim());
  if (!m || !m.slice(1).some(Boolean)) return null;
  const [d, h, mi] = [1, 2, 3].map((i) => Number(m[i] ?? 0));
  return d * 1440 + h * 60 + mi;
}
function flattenInstructions(v) {
  const out = [];
  const walk = (node, section) => {
    if (typeof node === "string") {
      for (const part of unescapeHtml(node.replace(/<[^>]+>/g, "\n")).split(/\n+/)) if (part.trim()) out.push({ section, text: part.trim() });
    } else if (Array.isArray(node)) node.forEach((n) => walk(n, section));
    else if (isObj(node)) {
      if (node["@type"] === "HowToSection" || "itemListElement" in node) walk(node.itemListElement ?? [], node.name || section);
      else walk(node.text || node.name || "", section);
    }
  };
  walk(v, null);
  return out;
}
export function clean(v) {
  if (typeof v === "string") return unescapeHtml(v.replace(/<[^>]+>/g, "")).trim();
  if (Array.isArray(v)) return v.map(clean);
  return v;
}
export function structuredFromJsonld(found) {
  let img = found.image;
  if (Array.isArray(img)) img = img[0] ?? null;
  if (isObj(img)) img = img.url;
  const y = found.recipeYield;
  const prep = isoMinutes(found.prepTime), cook = isoMinutes(found.cookTime);
  return {
    name: clean(found.name), description: clean(found.description),
    yield: clean(Array.isArray(y) && y.length ? y[0] : y),
    total_minutes: isoMinutes(found.totalTime) || (((prep || 0) + (cook || 0)) || null),
    prep_minutes: prep, cook_minutes: cook, image: typeof img === "string" ? img : null,
    category: clean(found.recipeCategory), cuisine: clean(found.recipeCuisine),
    ingredients: clean(found.recipeIngredient ?? []), instructions: flattenInstructions(found.recipeInstructions),
  };
}
export const ftsFallback = (q) => (q.match(/[\p{L}\p{N}_]+/gu) ?? []).map((w) => `"${w}"*`).join(" ");
