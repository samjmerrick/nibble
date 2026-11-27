// Convert a recipe into the family format with a language model (port of llm.py). Any OpenAI-compatible endpoint.
// Secrets/vars: LLM_API_KEY (without it the plain reader is used), LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT (seconds).
import * as R from "./recipe.js";

export class LlmError extends Error {}
export const enabled = (env) => !!(env.LLM_API_KEY ?? "").trim();

const prompt = (label, payload) =>
  "You are converting a recipe into our family's standard recipe format.\n\n" + R.FORMAT_GUIDE +
  "\nICONS (pick one for extra.icon): " + R.ICONS.join(", ") + "\n\n" +
  'Reply with ONLY one JSON object in the recipe_save shape (no id, status "draft"), with no text before or after it. Choose "category" from meals, baking, bread, ice-cream. Keep every quantity from the source; convert measures to grams as the format says, keeping the original in note. Write UK English.\n\n' +
  `${label}:\n${payload}`;

export function extractJson(text) {
  text = text.trim().replace(/^```(?:json)?\s*|\s*```\s*$/gm, "");
  const start = text.indexOf("{");
  if (start < 0) throw new LlmError("The model's answer had no recipe in it.");
  // find the matching closing brace of the first object
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (inStr) { if (esc) esc = false; else if (c === "\\") esc = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') inStr = true;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) {
      try { const o = JSON.parse(text.slice(start, i + 1)); if (o && typeof o === "object" && !Array.isArray(o)) return o; } catch { /* fall through */ }
      break;
    }
  }
  throw new LlmError("The model's answer wasn't valid JSON.");
}

async function complete(env, text) {
  const timeout = Number(env.LLM_TIMEOUT ?? 45);
  const base = (env.LLM_BASE_URL ?? "https://openrouter.ai/api/v1").replace(/\/$/, "");
  const model = (env.LLM_MODEL ?? "openrouter/free").trim();
  let r;
  try {
    r = await fetch(`${base}/chat/completions`, {
      method: "POST", headers: { Authorization: `Bearer ${env.LLM_API_KEY}`, "X-Title": "Nibble", "Content-Type": "application/json" },
      body: JSON.stringify({ model, messages: [{ role: "user", content: text }], temperature: 0.2 }), signal: AbortSignal.timeout(timeout * 1000),
    });
  } catch (e) {
    if (e?.name === "TimeoutError") throw new LlmError(`The model took longer than ${timeout} seconds.`);
    throw new LlmError("The model didn't answer.");
  }
  if (r.status === 429) throw new LlmError("The free model is busy or today's free limit is used up.");
  if (r.status >= 400) throw new LlmError(`The model service said ${r.status}.`);
  try { const d = await r.json(); return [d.choices[0].message.content || "", d.model || model]; }
  catch { throw new LlmError("The model service sent an unexpected answer."); }
}

export async function convert(env, label, payload) {
  if (!enabled(env)) throw new LlmError("No model is set up.");
  const [answer, model] = await complete(env, prompt(label, payload));
  const rec = extractJson(answer);
  rec.status = "draft"; delete rec.id;
  try { R.normalise(rec); } catch (e) { if (e instanceof R.UserError) throw new LlmError(`The model's recipe wasn't usable: ${e.message}`); throw e; }
  if (!(rec.components ?? []).some((c) => c && typeof c === "object" && ((c.steps ?? []).length || (c.ingredients ?? []).length))) throw new LlmError("The model's recipe had no ingredients or steps.");
  return [rec, model];
}
