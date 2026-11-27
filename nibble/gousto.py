"""fetch_page adapter for Gousto cookbook links.

Gousto's recipe pages are a JavaScript shell, so the recipe comes from Gousto's recipe API
instead and is returned as a schema.org Recipe in `jsonld`, like any other recipe page.
Amounts are for 2 people.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any

import webfetch

from .recipe import _int, clean

API = "https://production-api.gousto.co.uk/cmsreadbroker/v1/recipe/"


def slug(url: str) -> str | None:
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if host != "gousto.co.uk" and not host.endswith(".gousto.co.uk"):
        return None
    parts = [x for x in p.path.split("/") if x]
    if len(parts) >= 2 and parts[0] == "cookbook":
        return parts[-1]
    return None


def _lines(block: str) -> list[str]:
    """One Gousto instruction block -> its sentences, 2-portion wording only."""
    # "[s]", "[200ml]" etc. inside text-danger spans are the 4-portion variants.
    block = re.sub(r'<span class="text-danger">\s*\[[^\]]*\]\s*</span>', "", block)
    out = []
    for part in re.split(r"</p>|<br\s*/?>|\n", block):
        t = html.unescape(re.sub(r"<[^>]+>", "", part))
        t = re.sub(r"\s+", " ", t).strip()
        if not t or re.match(r"(?i)^(loved this recipe|share your|tag us|#)", t):
            continue
        out.append(t)
    return out


def parse(data: dict[str, Any], url: str) -> dict[str, Any]:
    """Gousto's recipe API JSON -> a fetch_page result with a schema.org Recipe."""
    e = (data.get("data") or {}).get("entry") or {}
    if not e.get("title"):
        raise ValueError("Gousto didn't return a recipe for that link")
    imgs = ((e.get("media") or {}).get("images")) or []
    img = max(imgs, key=lambda i: i.get("width") or 0).get("image") if imgs else None
    minutes = _int((e.get("prep_times") or {}).get("for_2"))
    ingredients = [clean(i.get("label") or i.get("name") or "") for i in e.get("ingredients") or []]
    basics = [b.get("title") for b in e.get("basics") or [] if b.get("title")]
    ingredients += [f"{b} (from the cupboard)" for b in basics]
    ingredients = [i for i in ingredients if i]
    steps = [line for ins in sorted(e.get("cooking_instructions") or [], key=lambda i: i.get("order") or 0)
             for line in _lines(ins.get("instruction") or "")]
    name, desc = clean(e.get("title")), clean(e.get("description"))
    recipe: dict[str, Any] = {
        "@context": "https://schema.org", "@type": "Recipe",
        "name": name, "description": desc, "recipeYield": "2 servings",
        "image": img, "recipeIngredient": ingredients,
        "recipeInstructions": [{"@type": "HowToStep", "text": s} for s in steps],
    }
    if minutes:
        recipe["totalTime"] = recipe["prepTime"] = f"PT{minutes}M"
    cuisine = (e.get("cuisine") or {}).get("title")
    if cuisine:
        recipe["recipeCuisine"] = cuisine
    text = "\n".join([name or "", desc or "", "Ingredients (2 people):", *ingredients, "Method:", *steps])
    return {
        "url": url, "title": name, "image": img, "jsonld": [recipe], "text": text, "text_truncated": False,
        "source": "gousto-api", "note": "Gousto amounts are for 2 people.",
    }


def fetch(url: str) -> dict[str, Any]:
    return parse(webfetch.get_json(API + urllib.parse.quote(slug(url) or "")), url)


def matches(url: str) -> bool:
    return slug(url) is not None
