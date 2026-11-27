// fetch_page adapter for Gousto cookbook links (port of gousto.py). Amounts are for 2 people.
import { getJson } from "./webfetch.js";
import { UserError, int, clean, unescapeHtml } from "./recipe.js";

const API = "https://production-api.gousto.co.uk/cmsreadbroker/v1/recipe/";

export function slug(url) {
  let u; try { u = new URL(url); } catch { return null; }
  const host = u.hostname.toLowerCase();
  if (host !== "gousto.co.uk" && !host.endsWith(".gousto.co.uk")) return null;
  const parts = u.pathname.split("/").filter(Boolean);
  return parts.length >= 2 && parts[0] === "cookbook" ? parts[parts.length - 1] : null;
}
export const matches = (url) => slug(url) !== null;

function lines(block) {
  block = block.replace(/<span class="text-danger">\s*\[[^\]]*\]\s*<\/span>/g, "");
  const out = [];
  for (const part of block.split(/<\/p>|<br\s*\/?>|\n/)) {
    const t = unescapeHtml(part.replace(/<[^>]+>/g, "")).replace(/\s+/g, " ").trim();
    if (!t || /^(loved this recipe|share your|tag us|#)/i.test(t)) continue;
    out.push(t);
  }
  return out;
}

export function parse(data, url) {
  const e = data?.data?.entry ?? {};
  if (!e.title) throw new UserError("Gousto didn't return a recipe for that link");
  const imgs = e.media?.images ?? [];
  const img = imgs.length ? imgs.reduce((a, b) => ((b.width ?? 0) > (a.width ?? 0) ? b : a)).image : null;
  const minutes = int(e.prep_times?.for_2);
  let ingredients = (e.ingredients ?? []).map((i) => clean(i.label || i.name || ""));
  ingredients.push(...(e.basics ?? []).filter((b) => b.title).map((b) => `${b.title} (from the cupboard)`));
  ingredients = ingredients.filter(Boolean);
  const steps = [...(e.cooking_instructions ?? [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0)).flatMap((i) => lines(i.instruction ?? ""));
  const name = clean(e.title), desc = clean(e.description);
  const recipe = { "@context": "https://schema.org", "@type": "Recipe", name, description: desc, recipeYield: "2 servings", image: img,
    recipeIngredient: ingredients, recipeInstructions: steps.map((s) => ({ "@type": "HowToStep", text: s })) };
  if (minutes) recipe.totalTime = recipe.prepTime = `PT${minutes}M`;
  if (e.cuisine?.title) recipe.recipeCuisine = e.cuisine.title;
  const text = [name || "", desc || "", "Ingredients (2 people):", ...ingredients, "Method:", ...steps].join("\n");
  return { url, title: name, image: img, jsonld: [recipe], text, text_truncated: false, source: "gousto-api", note: "Gousto amounts are for 2 people." };
}
export async function fetchGousto(url) { return parse(await getJson(API + encodeURIComponent(slug(url) ?? "")), url); }
