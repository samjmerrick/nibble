"""The family recipe format: guide, schema, normalising, and conversion to and from records.

A recipe is one record in project `nibble`, collection `recipes`. The record's title and tags
are the recipe's; everything else (category, status, components with ingredients and steps...)
is in `metadata`, checked by SCHEMA. `content` is a readable markdown version, regenerated
on every write, so search and Claudes that don't know the format still see the whole recipe.
"""

from __future__ import annotations

import html
import re
from typing import Any

PROJECT = "nibble"
COLLECTION = "recipes"

CATEGORIES = ("meals", "baking", "bread", "ice-cream")
STATUSES = ("draft", "live")
MADE = ("to-try", "made", "favourite")
DIFFICULTIES = ("easy", "medium", "hard")
ICONS = (
    "bowl", "pot", "pasta", "taco", "chicken",
    "cake", "loaf-cake", "muffin", "cookie", "bar", "bun", "cheesecake",
    "loaf", "boule", "ice-cream", "sorbet",
)

FORMAT_GUIDE = """\
Family recipe standard format (the Caribbean-style chicken recipe is the model):

- Split the recipe into components, each a named part with its own ingredients and steps,
  e.g. "Coconut Rice", "Caribbean Spiced Chicken", "Mango Salsa". Finish with a "Plating"
  component (steps only) when serving needs instructions. A simple recipe has one component.
- Every step is one short sentence with ONE action. Split "Chop the onion and fry it" into
  two steps. Keep one sentence only when the actions are a single motion ("Bring to the boil,
  then reduce the heat to very low and cover with a lid").
- Start steps with the verb. Keep the quantities and times from the source exactly. Never
  invent or change a quantity. Every ingredient should be used by some step.
- Put prep (boil the kettle, chop, zest) as its own early steps, like the source does.
- Timers: when a step has a fixed duration ("Cook for 9 minutes"), set timer_seconds (for a
  range like 12-15 minutes use the low end) and a short timer_label ("Chicken, first side").
- A timed or waiting step says how it ends. Fold the small action that ends the wait into the
  same step, so nothing is hidden behind a timer in cook mode: "Bake for 50 minutes, until a
  skewer comes out clean, then take it out of the oven." not "Bake for 50 minutes." followed by
  "Remove from the oven." This is the one exception to one action per step.
- Long or judged waits (proving, bulk fermentation, chilling, freezing overnight): set
  wait_min_minutes / wait_max_minutes and a cue describing how to judge readiness.
- Ingredients: qty as a number (0.5 not "1/2"), unit, item, and note for anything else
  ("half for coating, half for garnish"). Unquantified items like "Salt" have only item.
- Weights in grams. Every measured ingredient uses unit "g", including liquids (water, milk,
  oil, stock: 1 ml is about 1 g for water and milk, oil about 0.92 g/ml, honey and syrup about
  1.4 g/ml) and cups. Convert with a sensible density for that ingredient, round to a
  practical number (whole grams; 0.5 g steps under 10 g), and keep the original measure in
  note ("1 cup"). Change the step text to match ("Add 240 g milk").
- Teaspoons and tablespoons stay as spoons: qty in spoons, unit "tsp" or "tbsp", and the
  weight in grams as the first thing in note ("14 g" or "14 g, melted"). The app shows
  "1 tbsp butter (14 g, melted)" and scales both numbers with the servings.
- Counted things stay counts with no unit (2 eggs, 1 onion, 3 garlic cloves, 1 stock cube).
- Oven temperatures in Celsius. Convert Fahrenheit (round to the nearest 5 °C) and gas marks,
  write "180 °C" in the step text (add "(160 °C fan)" when the source gives a fan figure), and
  set temp_c on the step.
- Use UK spelling.
- Icon: set extra.icon to the closest of ICONS (see recipe_format_guide) for the recipe card.

Recipe JSON shape for recipe_save:
{
  "title": str, "category": "meals" | "baking" | "bread" | "ice-cream",
  "status": "draft" | "live", "made": "to-try" | "made" | "favourite",
  "difficulty": "easy" | "medium" | "hard" | null, "servings": int | null,
  "total_minutes": int | null, "source_url": str | null, "image_url": str | null,
  "description": str, "notes": str, "rating": number | null, "tags": [str],
  "extra": {"cuisine": "Caribbean", "icon": "chicken", ...},
  "components": [
    {"name": "Coconut Rice",
     "ingredients": [{"qty": 260, "unit": "g", "item": "white long grain rice", "note": null}],
     "steps": [{"text": "Cook for 12-15 minutes, until the stock is absorbed.",
                "timer_seconds": 720, "timer_label": "Rice"}]}
  ]
}
"""

PROJECT_GUIDE = (
    "Nibble, the family recipe app. Each recipe is one record in the `recipes` collection: the recipe's "
    "title and tags on the record, everything else (category, status, components with ingredients and "
    "steps, ...) in `metadata`, which must match the collection's schema (see describe_project). Put a "
    "readable markdown version of the recipe in `content` (title, ingredients, numbered steps): search "
    "reads it. New recipes should have metadata.status \"draft\" until someone approves them. Use `store` "
    "to add and `update` (with replace_metadata) to change a recipe.\n\n" + FORMAT_GUIDE
    + "\nICONS: " + ", ".join(ICONS)
)

_STR_OR_NULL = {"type": ["string", "null"]}
_INT_OR_NULL = {"type": ["integer", "null"]}
_NUM_OR_NULL = {"type": ["number", "null"]}

SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["category", "status", "made", "components"],
    "additionalProperties": False,
    "properties": {
        "category": {"enum": list(CATEGORIES)},
        "status": {"enum": list(STATUSES)},
        "made": {"enum": list(MADE)},
        "difficulty": {"enum": [*DIFFICULTIES, None]},
        "servings": _INT_OR_NULL,
        "total_minutes": _INT_OR_NULL,
        "source_url": _STR_OR_NULL,
        "image_url": _STR_OR_NULL,
        "description": {"type": "string"},
        "notes": {"type": "string"},
        "rating": _NUM_OR_NULL,
        "extra": {"type": "object"},
        "origin": _STR_OR_NULL,
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "ingredients", "steps"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "ingredients": {"type": "array", "items": {
                        "type": "object", "required": ["item"], "additionalProperties": False,
                        "properties": {
                            "qty": {"type": "number"}, "qty_max": {"type": "number"}, "unit": {"type": "string"},
                            "item": {"type": "string", "minLength": 1}, "note": {"type": "string"},
                        },
                    }},
                    "steps": {"type": "array", "items": {
                        "type": "object", "required": ["text"], "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string", "minLength": 1},
                            "timer_seconds": {"type": "integer"}, "timer_label": {"type": "string"},
                            "wait_min_minutes": {"type": "integer"}, "wait_max_minutes": {"type": "integer"},
                            "cue": {"type": "string"}, "temp_c": {"type": "number"},
                        },
                    }},
                },
            },
        },
    },
}

SEARCH_FIELDS = ["components[].name", "components[].ingredients[].item", "components[].steps[].text",
                 "description", "extra.cuisine"]

TOP_FIELDS = ("category", "status", "made", "difficulty", "servings", "total_minutes", "source_url", "image_url",
              "description", "notes", "rating", "extra", "origin")


# --------------------------------------------------------------------------- value helpers


def _tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    return sorted({str(t).strip().lower() for t in tags if str(t).strip()})


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    f = _num(v)
    return None if f is None else int(round(f))


def _str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def choice(v: Any, allowed: tuple[str, ...], field: str, default: str | None = None) -> str | None:
    if v is None or v == "":
        return default
    s = str(v).strip().lower()
    if s not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)} (got {v!r})")
    return s


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# --------------------------------------------------------------------------- normalise


def normalise(recipe: dict[str, Any]) -> tuple[str, list[str], dict[str, Any]]:
    """A recipe in the recipe_save shape -> (title, tags, metadata), checked and tidied."""
    if not isinstance(recipe, dict):
        raise ValueError("recipe must be an object")
    title = _str(recipe.get("title"))
    if not title:
        raise ValueError("title is required")
    category = choice(recipe.get("category"), CATEGORIES, "category")
    if not category:
        raise ValueError(f"category is required: one of {', '.join(CATEGORIES)}")
    comps = recipe.get("components") or []
    if not isinstance(comps, list):
        raise ValueError("components must be a list")
    components = []
    for c in comps:
        if not isinstance(c, dict):
            raise ValueError("each component must be an object")
        ings, steps = [], []
        for i in c.get("ingredients") or []:
            if isinstance(i, str):
                i = {"item": i}
            if not isinstance(i, dict):
                raise ValueError("each ingredient must be an object or a string")
            item = _str(i.get("item"))
            if item:
                ings.append(_drop_none({"qty": _num(i.get("qty")), "qty_max": _num(i.get("qty_max")),
                                        "unit": _str(i.get("unit")), "item": item, "note": _str(i.get("note"))}))
        for s in c.get("steps") or []:
            if isinstance(s, str):
                s = {"text": s}
            if not isinstance(s, dict):
                raise ValueError("each step must be an object or a string")
            text = _str(s.get("text"))
            if text:
                steps.append(_drop_none({
                    "text": text, "timer_seconds": _int(s.get("timer_seconds")), "timer_label": _str(s.get("timer_label")),
                    "wait_min_minutes": _int(s.get("wait_min_minutes")), "wait_max_minutes": _int(s.get("wait_max_minutes")),
                    "cue": _str(s.get("cue")), "temp_c": _num(s.get("temp_c")),
                }))
        components.append({"name": str(c.get("name") or ""), "ingredients": ings, "steps": steps})
    extra = recipe.get("extra") if isinstance(recipe.get("extra"), dict) else {}
    meta = {
        "category": category,
        "status": choice(recipe.get("status"), STATUSES, "status", "draft"),
        "made": choice(recipe.get("made"), MADE, "made", "to-try"),
        "difficulty": choice(recipe.get("difficulty"), DIFFICULTIES, "difficulty"),
        "servings": _int(recipe.get("servings")),
        "total_minutes": _int(recipe.get("total_minutes")),
        "source_url": _str(recipe.get("source_url")),
        "image_url": _str(recipe.get("image_url")),
        "description": str(recipe.get("description") or ""),
        "notes": str(recipe.get("notes") or ""),
        "rating": _num(recipe.get("rating")),
        "extra": extra,
        "origin": _str(recipe.get("origin")),
        "components": components,
    }
    return title, _tags(recipe.get("tags")), meta


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def render(title: str, meta: dict[str, Any]) -> str:
    """Readable markdown for the record's content."""
    out = []
    if meta.get("description"):
        out += [meta["description"], ""]
    facts = [meta["category"]]
    if meta.get("servings"):
        facts.append(f"serves {meta['servings']}")
    if meta.get("total_minutes"):
        facts.append(f"{meta['total_minutes']} minutes")
    if meta.get("difficulty"):
        facts.append(meta["difficulty"])
    facts.append(meta["status"])
    out += [" · ".join(facts), ""]
    if meta.get("source_url"):
        out += [f"Source: {meta['source_url']}", ""]
    for c in meta.get("components") or []:
        out.append(f"## {c['name'] or 'Method'}")
        if c["ingredients"]:
            out.append("")
            for i in c["ingredients"]:
                qty = ""
                if "qty" in i:
                    qty = _fmt_num(i["qty"]) + (f"-{_fmt_num(i['qty_max'])}" if "qty_max" in i else "")
                    qty += (" " + i["unit"]) if i.get("unit") else ""
                line = " ".join(x for x in (qty, i["item"]) if x)
                out.append(f"- {line}" + (f" ({i['note']})" if i.get("note") else ""))
        if c["steps"]:
            out.append("")
            for n, s in enumerate(c["steps"], 1):
                out.append(f"{n}. {s['text']}" + (f" ({s['cue']})" if s.get("cue") else ""))
        out.append("")
    if meta.get("notes"):
        out += ["## Notes", "", meta["notes"], ""]
    return "\n".join(out).strip() + "\n"


def before_write(rec: dict[str, Any]) -> dict[str, Any]:
    """Records hook: normalise any write to the recipes collection and refresh its content."""
    title, tags, meta = normalise({**(rec.get("metadata") or {}), "title": rec.get("title"), "tags": rec.get("tags")})
    return {**rec, "title": title, "tags": tags, "metadata": meta, "content": render(title, meta)}


def summary(record: dict[str, Any]) -> dict[str, Any]:
    m = record["metadata"]
    return {
        "id": record["id"], "title": record["title"], "category": m.get("category"), "status": m.get("status"),
        "made": m.get("made"), "difficulty": m.get("difficulty"), "servings": m.get("servings"),
        "total_minutes": m.get("total_minutes"), "source_url": m.get("source_url"), "rating": m.get("rating"),
        "tags": record["tags"], "updated_at": record["updated_at"],
        "extra": {k: v for k, v in (m.get("extra") or {}).items() if k in ("cuisine", "icon")},
    }


def to_recipe(record: dict[str, Any]) -> dict[str, Any]:
    """A record -> the recipe shape recipe_get returns."""
    m = record["metadata"]
    rec = summary(record)
    rec.update(description=m.get("description", ""), notes=m.get("notes", ""), image_url=m.get("image_url"),
               extra=m.get("extra") or {}, origin=m.get("origin"), created_at=record["created_at"])
    rec["components"] = m.get("components") or []
    return rec


def to_metadata(recipe: dict[str, Any]) -> dict[str, Any]:
    """The recipe_save shape -> the metadata part (the hook does the checking)."""
    return {k: recipe.get(k) for k in (*TOP_FIELDS, "components") if k in recipe}


# --------------------------------------------------------------------------- schema.org import


def find_recipe(node: Any) -> dict[str, Any] | None:
    if isinstance(node, list):
        for n in node:
            r = find_recipe(n)
            if r:
                return r
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "Recipe" in types:
            return node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                r = find_recipe(node[key])
                if r:
                    return r
    return None


def _iso_minutes(v: Any) -> int | None:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?", str(v or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mi


def _flatten_instructions(v: Any) -> list[dict[str, Any]]:
    """schema.org recipeInstructions -> [{"section": name|None, "text": ...}]"""
    out: list[dict[str, Any]] = []

    def walk(node: Any, section: str | None) -> None:
        if isinstance(node, str):
            for part in re.split(r"\n+", html.unescape(re.sub(r"<[^>]+>", "\n", node))):
                if part.strip():
                    out.append({"section": section, "text": part.strip()})
        elif isinstance(node, list):
            for n in node:
                walk(n, section)
        elif isinstance(node, dict):
            if node.get("@type") == "HowToSection" or "itemListElement" in node:
                walk(node.get("itemListElement") or [], node.get("name") or section)
            else:
                walk(node.get("text") or node.get("name") or "", section)

    walk(v, None)
    return out


def clean(v: Any) -> Any:
    if isinstance(v, str):
        return html.unescape(re.sub(r"<[^>]+>", "", v)).strip()
    if isinstance(v, list):
        return [clean(x) for x in v]
    return v


def structured_from_jsonld(found: dict[str, Any]) -> dict[str, Any]:
    img = found.get("image")
    if isinstance(img, list):
        img = img[0] if img else None
    if isinstance(img, dict):
        img = img.get("url")
    y = found.get("recipeYield")
    return {
        "name": clean(found.get("name")),
        "description": clean(found.get("description")),
        "yield": clean(y[0] if isinstance(y, list) and y else y),
        "total_minutes": _iso_minutes(found.get("totalTime"))
        or ((_iso_minutes(found.get("prepTime")) or 0) + (_iso_minutes(found.get("cookTime")) or 0) or None),
        "prep_minutes": _iso_minutes(found.get("prepTime")),
        "cook_minutes": _iso_minutes(found.get("cookTime")),
        "image": img if isinstance(img, str) else None,
        "category": clean(found.get("recipeCategory")),
        "cuisine": clean(found.get("recipeCuisine")),
        "ingredients": clean(found.get("recipeIngredient") or []),
        "instructions": _flatten_instructions(found.get("recipeInstructions")),
    }


def fts_fallback(query: str) -> str:
    """Turn arbitrary text into a safe FTS5 query (every word, prefix-matched, ANDed)."""
    words = re.findall(r"\w+", query, flags=re.UNICODE)
    return " ".join(f'"{w}"*' for w in words)
