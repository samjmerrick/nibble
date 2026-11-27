"""Turn pasted recipe text into a draft recipe, without an AI model.

The artifact version of the page asked Claude to convert pasted text. The web app has no Claude, so this
reads the common shape of a written recipe instead: a title line, "Serves 4", an "Ingredients" heading
(with optional "For the sauce" sub-headings), and a "Method" heading with numbered or one-per-line steps.
Quantities are kept exactly as written (no unit conversion); the result is always a draft to check.
"""

from __future__ import annotations

import re
from typing import Any

GLYPHS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125, "⅕": 0.2}
UNITS = {
    "g": "g", "gram": "g", "grams": "g", "gr": "g", "kg": "kg", "kilo": "kg", "kilos": "kg", "kilograms": "kg",
    "ml": "ml", "millilitres": "ml", "milliliters": "ml", "l": "l", "litre": "l", "litres": "l", "liter": "l",
    "liters": "l", "cl": "cl", "dl": "dl",
    "tsp": "tsp", "tsps": "tsp", "teaspoon": "tsp", "teaspoons": "tsp", "t": "tsp",
    "tbsp": "tbsp", "tbsps": "tbsp", "tbs": "tbsp", "tbl": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "dsp": "dsp", "cup": "cup", "cups": "cup", "oz": "oz", "ounce": "oz", "ounces": "oz", "lb": "lb", "lbs": "lb",
    "pound": "lb", "pounds": "lb", "pint": "pint", "pints": "pint", "pinch": "pinch", "pinches": "pinch",
    "handful": "handful", "handfuls": "handful", "clove": "clove", "cloves": "clove", "tin": "tin", "tins": "tin",
    "can": "tin", "cans": "tin", "pack": "pack", "packs": "pack", "bunch": "bunch", "bunches": "bunch",
    "slice": "slice", "slices": "slice", "sprig": "sprig", "sprigs": "sprig", "stick": "stick", "sticks": "stick",
}
_NUM = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?\s*[½¼¾⅓⅔⅛⅕]?|[½¼¾⅓⅔⅛⅕])"
_QTY = re.compile(rf"^(?P<q>{_NUM})(?:\s*(?:-|–|to)\s*(?P<q2>{_NUM}))?\s*(?P<rest>.*)$")
_UNIT = re.compile(r"^(?P<u>[a-zA-Z]+)\.?(?=[\s,(]|$)\s*(?:of\s+)?(?P<rest>.*)$")
_BULLET = re.compile(r"^\s*(?:[-*•·▢□☐◦‣–]|\d+[.)]|step\s*\d+[:.)]?)\s*", re.I)
_NUMBERED = re.compile(r"^\s*(?:step\s*)?\d+\s*[.):]\s*|^\s*step\s*\d+\s*", re.I)

ING_HEAD = re.compile(r"^(?:ingredients?|you(?:'|’)ll need|you will need|what you need)\b", re.I)
STEP_HEAD = re.compile(r"^(?:method|instructions?|directions?|steps?|preparation|how to make(?: it)?|to make)\b", re.I)
NOTE_HEAD = re.compile(r"^(?:notes?|tips?|cook(?:'|’)s notes?|to serve|serving suggestions?)\b", re.I)
SERVES = re.compile(r"\b(?:serves|servings?|makes|yield|portions?)\s*:?\s*(\d+)", re.I)
TIME = re.compile(r"\b(?:total time|ready in|takes)\s*:?\s*(?:(\d+)\s*(?:h|hr|hrs|hours?))?\s*(?:(\d+)\s*(?:m|min|mins|minutes?))?", re.I)


def _num(s: str) -> float:
    s = s.strip().replace(",", ".")
    total = 0.0
    for part in s.split():
        if "/" in part:
            a, b = part.split("/", 1)
            total += int(a) / int(b) if int(b) else 0
        else:
            g = part[-1] if part[-1] in GLYPHS else ""
            if g:
                part = part[:-1]
            total += (float(part) if part else 0) + GLYPHS.get(g, 0)
    total = round(total, 3)
    return int(total) if total == int(total) else total


def parse_ingredient(line: str) -> dict[str, Any]:
    """'200 g plain flour, sifted' -> {"qty": 200, "unit": "g", "item": "plain flour", "note": "sifted"}"""
    text = _BULLET.sub("", line).strip()
    out: dict[str, Any] = {}
    m = _QTY.match(text)
    if m:
        out["qty"] = _num(m["q"])
        if m["q2"]:
            out["qty_max"] = _num(m["q2"])
        text = m["rest"]
        # "2 x 400g tins chopped tomatoes" -> 2 tin chopped tomatoes (400 g)
        if x := re.match(r"^[x×]\s*(\d+(?:[.,]\d+)?)\s*(g|kg|ml|l)\b\s*(?P<rest>.*)$", text, re.I):
            size = f"{x.group(1)} {x.group(2).lower()}"
            u = _UNIT.match(x["rest"])
            if u and u["u"].lower() in UNITS:
                out["unit"], text = UNITS[u["u"].lower()], u["rest"]
            else:
                text = x["rest"]
            text = f"{text} ({size})"
        # "200g" leaves "g plain flour"
        u = None if "unit" in out else _UNIT.match(text)
        if u and u["u"].lower() in UNITS and not (u["u"].lower() == "t" and u["u"] != "t"):
            out["unit"] = UNITS[u["u"].lower()]
            text = u["rest"]
    text = text.strip()
    note = None
    if lead := re.match(r"^\(([^)]*)\)\s*(.+)$", text):  # "2 cups (250g) plain flour"
        note, text = lead.group(1).strip(), lead.group(2)
    paren = re.search(r"\s*\(([^)]*)\)\s*$", text)
    if paren and paren.start() > 0:
        note, text = ", ".join(x for x in (note, paren.group(1).strip()) if x), text[: paren.start()]
    if "," in text:
        text, more = text.split(",", 1)
        note = ", ".join(x for x in (more.strip(), note) if x)
    out["item"] = text.strip(" .;:-") or line.strip()
    if note:
        out["note"] = note
    return out


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace(" ", " ")).strip()


def _heading(line: str) -> str:
    return line.rstrip(":").strip().strip("*#").strip()


def _looks_like_ingredient(line: str) -> bool:
    t = _BULLET.sub("", line)
    return bool(_QTY.match(t)) and len(t.split()) <= 14 and not t.rstrip().endswith(".")


def _is_subheading(line: str) -> bool:
    """'For the sauce:', 'Sauce:', 'FILLING' (short, no quantity, ends with a colon or is a 'For the' line)."""
    t = _heading(line)
    if not t or len(t.split()) > 6 or _QTY.match(t):
        return False
    return line.rstrip().endswith(":") or bool(re.match(r"^for (?:the )?\w", t, re.I)) or (t.isupper() and len(t) > 2)


def _split_steps(lines: list[str]) -> list[str]:
    """Numbered lines are steps; otherwise each line (or paragraph) is one."""
    steps: list[str] = []
    numbered = sum(1 for x in lines if _NUMBERED.match(x)) >= max(2, len(lines) // 3)
    for line in lines:
        if numbered and not _NUMBERED.match(line) and steps and not _is_subheading(line):
            steps[-1] += " " + line  # a numbered step wrapped onto the next line
            continue
        t = _BULLET.sub("", line).strip()
        if t:
            steps.append(t)
    return steps


def parse_text(text: str, source_url: str | None = None) -> dict[str, Any]:
    lines = [_clean(x) for x in (text or "").replace("\r", "").split("\n")]
    lines = [x for x in lines if x]
    if not lines:
        raise ValueError("Paste the recipe text first.")

    title, description, notes = None, [], []
    servings = total = None
    comps: list[dict[str, Any]] = []
    section = "intro"
    cur: dict[str, Any] | None = None

    def comp(name: str = "") -> dict[str, Any]:
        nonlocal cur
        # reuse an existing component with that name (ingredients first, then its method)
        for c in comps:
            if c["name"].lower() == name.lower():
                cur = c
                return c
        cur = {"name": name, "ingredients": [], "_steps": []}
        comps.append(cur)
        return cur

    for line in lines:
        h = _heading(line)
        if m := SERVES.search(line):
            if servings is None and len(line.split()) <= 8:
                servings = int(m.group(1))
                continue
        if (m := TIME.search(line)) and any(m.groups()) and len(line.split()) <= 8:
            total = total or (int(m.group(1) or 0) * 60 + int(m.group(2) or 0)) or None
            continue
        if ING_HEAD.match(h) and len(h.split()) <= 6:
            section = "ing"
            rest = re.sub(ING_HEAD, "", h).strip(" :-–")
            rest = re.sub(r"^for (?:the )?", "", rest, flags=re.I)
            comp(rest.capitalize() if rest else (cur["name"] if cur else ""))
            continue
        if STEP_HEAD.match(h) and len(h.split()) <= 6:
            section = "step"
            if cur is None:
                comp("")
            elif len(comps) > 1:
                comp("")  # a shared method after several ingredient groups
            continue
        if NOTE_HEAD.match(h) and len(h.split()) <= 4:
            section = "notes"
            continue
        if section == "intro":
            if title is None:
                title = h
            elif _looks_like_ingredient(line):
                section = "ing"
                comp("").setdefault("ingredients", []).append(parse_ingredient(line))
            else:
                description.append(line)
            continue
        if section in ("ing", "step") and _is_subheading(line):
            name = re.sub(r"^for (?:the )?", "", _heading(line), flags=re.I).strip()
            comp(name[:1].upper() + name[1:].lower() if name.isupper() else name[:1].upper() + name[1:])
            continue
        if section == "ing":
            if not _looks_like_ingredient(line) and (line.endswith(".") or len(line.split()) > 8):
                section = "step"  # method with no heading
                cur["_steps"].append(line)
            else:
                cur["ingredients"].append(parse_ingredient(line))
        elif section == "step":
            cur["_steps"].append(line)
        else:
            notes.append(line)

    for c in comps:
        c["steps"] = [{"text": s} for s in _split_steps(c.pop("_steps"))]
    comps = [c for c in comps if c["ingredients"] or c["steps"]]
    if not comps:
        raise ValueError("Couldn't find ingredients or a method in that text. Put them under "
                         "\"Ingredients\" and \"Method\" headings, one per line, and try again.")
    return {
        "title": title or "Untitled recipe",
        "category": "meals",
        "status": "draft",
        "description": " ".join(description)[:600],
        "servings": servings,
        "total_minutes": total,
        "source_url": source_url or None,
        "notes": "\n".join(notes + ["Added from pasted text; check the quantities and steps."]),
        "components": comps,
    }
