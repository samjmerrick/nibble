"""Convert a recipe into the family format with a language model (OpenRouter's free models by default).

Any OpenAI-compatible chat endpoint works. Settings (in .env):
    LLM_API_KEY   the key; without it Nibble uses its own text reader and never calls a model
    LLM_BASE_URL  default https://openrouter.ai/api/v1
    LLM_MODEL     default openrouter/free (OpenRouter's router across its free models); any model id works,
                  e.g. a specific ":free" model, or anthropic/claude-... for paid Claude through OpenRouter
    LLM_TIMEOUT   seconds per request, default 90
Every answer is checked with recipe.normalise before it is shown; anything unusable raises LlmError and the
caller falls back to the plain reader, so an import always gives a draft.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx2 as httpx

import recipe as R

API_KEY = os.environ.get("LLM_API_KEY", "").strip()
BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "openrouter/free").strip()
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "90"))


class LlmError(Exception):
    pass


def enabled() -> bool:
    return bool(API_KEY)


def prompt(source_label: str, payload: str) -> str:
    return (
        "You are converting a recipe into our family's standard recipe format.\n\n" + R.FORMAT_GUIDE
        + "\nICONS (pick one for extra.icon): " + ", ".join(R.ICONS) + "\n\n"
        'Reply with ONLY one JSON object in the recipe_save shape (no id, status "draft"), with no text before '
        'or after it. Choose "category" from meals, baking, bread, ice-cream. Keep every quantity from the '
        "source; convert measures to grams as the format says, keeping the original in note. Write UK English.\n\n"
        f"{source_label}:\n{payload}"
    )


def extract_json(text: str) -> dict[str, Any]:
    """The first JSON object in a model's answer (models like to add code fences or a sentence)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.M)
    start = text.find("{")
    if start < 0:
        raise LlmError("The model's answer had no recipe in it.")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError as e:
        raise LlmError("The model's answer wasn't valid JSON.") from e
    if not isinstance(obj, dict):
        raise LlmError("The model's answer wasn't a recipe.")
    return obj


async def complete(text: str) -> tuple[str, str]:
    """(answer text, model that answered)."""
    headers = {"Authorization": f"Bearer {API_KEY}", "X-Title": "Nibble"}
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}], "temperature": 0.2}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{BASE_URL}/chat/completions", json=body, headers=headers)
    except Exception as e:  # network errors, timeouts
        raise LlmError(f"The model didn't answer ({type(e).__name__}).") from e
    if r.status_code == 429:
        raise LlmError("The free model is busy or today's free limit is used up.")
    if r.status_code >= 400:
        raise LlmError(f"The model service said {r.status_code}.")
    try:
        data = r.json()
        return data["choices"][0]["message"]["content"] or "", data.get("model") or MODEL
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LlmError("The model service sent an unexpected answer.") from e


async def convert(source_label: str, payload: str) -> tuple[dict[str, Any], str]:
    """A recipe in the recipe_save shape (checked) and the model that wrote it."""
    if not enabled():
        raise LlmError("No model is set up.")
    answer, model = await complete(prompt(source_label, payload))
    rec = extract_json(answer)
    rec["status"] = "draft"
    rec.pop("id", None)
    try:
        R.normalise(rec)
    except ValueError as e:
        raise LlmError(f"The model's recipe wasn't usable: {e}") from e
    if not any(c.get("steps") or c.get("ingredients") for c in rec.get("components") or [] if isinstance(c, dict)):
        raise LlmError("The model's recipe had no ingredients or steps.")
    return rec, model
