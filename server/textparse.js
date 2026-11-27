// Turn pasted recipe text into a draft recipe, without an AI model (port of textparse.py).
// Reads the common shape of a written recipe: title line, "Serves 4", an "Ingredients" heading (with optional
// "For the sauce" sub-headings) and a "Method" heading with numbered or one-per-line steps. Quantities kept as written.
import { UserError } from "./recipe.js";

const GLYPHS = { "½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125, "⅕": 0.2 };
const UNITS = {
  g: "g", gram: "g", grams: "g", gr: "g", kg: "kg", kilo: "kg", kilos: "kg", kilograms: "kg",
  ml: "ml", millilitres: "ml", milliliters: "ml", l: "l", litre: "l", litres: "l", liter: "l", liters: "l", cl: "cl", dl: "dl",
  tsp: "tsp", tsps: "tsp", teaspoon: "tsp", teaspoons: "tsp", t: "tsp",
  tbsp: "tbsp", tbsps: "tbsp", tbs: "tbsp", tbl: "tbsp", tablespoon: "tbsp", tablespoons: "tbsp",
  dsp: "dsp", cup: "cup", cups: "cup", oz: "oz", ounce: "oz", ounces: "oz", lb: "lb", lbs: "lb",
  pound: "lb", pounds: "lb", pint: "pint", pints: "pint", pinch: "pinch", pinches: "pinch",
  handful: "handful", handfuls: "handful", clove: "clove", cloves: "clove", tin: "tin", tins: "tin",
  can: "tin", cans: "tin", pack: "pack", packs: "pack", bunch: "bunch", bunches: "bunch",
  slice: "slice", slices: "slice", sprig: "sprig", sprigs: "sprig", stick: "stick", sticks: "stick",
};
const NUM = String.raw`(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?\s*[½¼¾⅓⅔⅛⅕]?|[½¼¾⅓⅔⅛⅕])`;
const QTY = new RegExp(String.raw`^(?<q>${NUM})(?:\s*(?:-|–|to)\s*(?<q2>${NUM}))?\s*(?<rest>.*)$`, "s");
const UNIT = /^(?<u>[a-zA-Z]+)\.?(?=[\s,(]|$)\s*(?:of\s+)?(?<rest>.*)$/s;
const BULLET = /^\s*(?:[-*•·▢□☐◦‣–]|\d+[.)]|step\s*\d+[:.)]?)\s*/i;
const NUMBERED = /^\s*(?:step\s*)?\d+\s*[.):]\s*|^\s*step\s*\d+\s*/i;
const ING_HEAD = /^(?:ingredients?|you(?:'|’)ll need|you will need|what you need)\b/i;
const STEP_HEAD = /^(?:method|instructions?|directions?|steps?|preparation|how to make(?: it)?|to make)\b/i;
const NOTE_HEAD = /^(?:notes?|tips?|cook(?:'|’)s notes?|to serve|serving suggestions?)\b/i;
const SERVES = /\b(?:serves|servings?|makes|yield|portions?)\s*:?\s*(\d+)/i;
const TIME = /\b(?:total time|ready in|takes)\s*:?\s*(?:(\d+)\s*(?:h|hr|hrs|hours?))?\s*(?:(\d+)\s*(?:m|min|mins|minutes?))?/i;

function num(s) {
  s = s.trim().replace(",", ".");
  let total = 0;
  for (let part of s.split(/\s+/)) {
    if (part.includes("/")) {
      const [a, b] = part.split("/");
      total += Number(b) ? Number(a) / Number(b) : 0;
    } else {
      const last = part[part.length - 1];
      const g = last in GLYPHS ? last : "";
      if (g) part = part.slice(0, -1);
      total += (part ? parseFloat(part) : 0) + (GLYPHS[g] ?? 0);
    }
  }
  total = Math.round(total * 1000) / 1000;
  return total;
}

export function parseIngredient(line) {
  let text = line.replace(BULLET, "").trim();
  const out = {};
  const m = QTY.exec(text);
  if (m) {
    out.qty = num(m.groups.q);
    if (m.groups.q2) out.qty_max = num(m.groups.q2);
    text = m.groups.rest;
    const x = /^[x×]\s*(\d+(?:[.,]\d+)?)\s*(g|kg|ml|l)\b\s*(?<rest>.*)$/is.exec(text);
    if (x) {
      const size = `${x[1]} ${x[2].toLowerCase()}`;
      const u = UNIT.exec(x.groups.rest);
      if (u && u.groups.u.toLowerCase() in UNITS) { out.unit = UNITS[u.groups.u.toLowerCase()]; text = u.groups.rest; }
      else text = x.groups.rest;
      text = `${text} (${size})`;
    }
    const u = "unit" in out ? null : UNIT.exec(text);
    if (u && u.groups.u.toLowerCase() in UNITS && !(u.groups.u.toLowerCase() === "t" && u.groups.u !== "t")) {
      out.unit = UNITS[u.groups.u.toLowerCase()];
      text = u.groups.rest;
    }
  }
  text = text.trim();
  let note = null;
  const lead = /^\(([^)]*)\)\s*(.+)$/s.exec(text);
  if (lead) { note = lead[1].trim(); text = lead[2]; }
  const paren = /\s*\(([^)]*)\)\s*$/.exec(text);
  if (paren && paren.index > 0) { note = [note, paren[1].trim()].filter(Boolean).join(", "); text = text.slice(0, paren.index); }
  if (text.includes(",")) {
    const i = text.indexOf(",");
    const more = text.slice(i + 1);
    text = text.slice(0, i);
    note = [more.trim(), note].filter(Boolean).join(", ");
  }
  out.item = text.replace(/^[ .;:-]+|[ .;:-]+$/g, "") || line.trim();
  if (note) out.note = note;
  return out;
}

const clean = (line) => line.replace(/ /g, " ").replace(/\s+/g, " ").trim();
const heading = (line) => line.replace(/:+$/, "").trim().replace(/^[*#]+|[*#]+$/g, "").trim();
const words = (s) => s.split(/\s+/).filter(Boolean).length;
const looksLikeIngredient = (line) => { const t = line.replace(BULLET, ""); return QTY.test(t) && words(t) <= 14 && !t.trimEnd().endsWith("."); };
function isSubheading(line) {
  const t = heading(line);
  if (!t || words(t) > 6 || QTY.test(t)) return false;
  return line.trimEnd().endsWith(":") || /^for (?:the )?\w/i.test(t) || (t === t.toUpperCase() && t !== t.toLowerCase() && t.length > 2);
}
function splitSteps(lines) {
  const steps = [];
  const numbered = lines.filter((x) => NUMBERED.test(x)).length >= Math.max(2, Math.floor(lines.length / 3));
  for (const line of lines) {
    if (numbered && !NUMBERED.test(line) && steps.length && !isSubheading(line)) { steps[steps.length - 1] += " " + line; continue; }
    const t = line.replace(BULLET, "").trim();
    if (t) steps.push(t);
  }
  return steps;
}
const cap = (name) => (name === name.toUpperCase() && name !== name.toLowerCase() ? name.slice(0, 1).toUpperCase() + name.slice(1).toLowerCase() : name.slice(0, 1).toUpperCase() + name.slice(1));

export function parseText(text, sourceUrl = null) {
  const lines = (text || "").replace(/\r/g, "").split("\n").map(clean).filter(Boolean);
  if (!lines.length) throw new UserError("Paste the recipe text first.");
  let title = null, servings = null, total = null, section = "intro", cur = null;
  const description = [], notes = [], comps = [];
  const comp = (name = "") => {
    for (const c of comps) if (c.name.toLowerCase() === name.toLowerCase()) { cur = c; return c; }
    cur = { name, ingredients: [], _steps: [] };
    comps.push(cur);
    return cur;
  };
  for (const line of lines) {
    const h = heading(line);
    let m = SERVES.exec(line);
    if (m && servings === null && words(line) <= 8) { servings = parseInt(m[1], 10); continue; }
    m = TIME.exec(line);
    if (m && (m[1] || m[2]) && words(line) <= 8) { total = total || (parseInt(m[1] || "0", 10) * 60 + parseInt(m[2] || "0", 10)) || null; continue; }
    if (ING_HEAD.test(h) && words(h) <= 6) {
      section = "ing";
      let rest = h.replace(ING_HEAD, "").replace(/^[ :\-–]+|[ :\-–]+$/g, "");
      rest = rest.replace(/^for (?:the )?/i, "");
      comp(rest ? rest.slice(0, 1).toUpperCase() + rest.slice(1).toLowerCase() : cur ? cur.name : "");
      continue;
    }
    if (STEP_HEAD.test(h) && words(h) <= 6) {
      section = "step";
      if (cur === null) comp("");
      else if (comps.length > 1) comp("");
      continue;
    }
    if (NOTE_HEAD.test(h) && words(h) <= 4) { section = "notes"; continue; }
    if (section === "intro") {
      if (title === null) title = h;
      else if (looksLikeIngredient(line)) { section = "ing"; comp("").ingredients.push(parseIngredient(line)); }
      else description.push(line);
      continue;
    }
    if ((section === "ing" || section === "step") && isSubheading(line)) {
      comp(cap(heading(line).replace(/^for (?:the )?/i, "").trim()));
      continue;
    }
    if (section === "ing") {
      if (!looksLikeIngredient(line) && (line.endsWith(".") || words(line) > 8)) { section = "step"; cur._steps.push(line); }
      else cur.ingredients.push(parseIngredient(line));
    } else if (section === "step") cur._steps.push(line);
    else notes.push(line);
  }
  let outComps = comps.map((c) => ({ name: c.name, ingredients: c.ingredients, steps: splitSteps(c._steps).map((s) => ({ text: s })) }));
  outComps = outComps.filter((c) => c.ingredients.length || c.steps.length);
  if (!outComps.length) throw new UserError('Couldn\'t find ingredients or a method in that text. Put them under "Ingredients" and "Method" headings, one per line, and try again.');
  return {
    title: title || "Untitled recipe", category: "meals", status: "draft", description: description.join(" ").slice(0, 600),
    servings, total_minutes: total, source_url: sourceUrl || null,
    notes: [...notes, "Added from pasted text; check the quantities and steps."].join("\n"), components: outComps,
  };
}
