"""End-to-end tests: a real bee-database server (from ../bee-database/server, or BEE_SERVER_DIR) on a
fresh database, and Nibble running against it, signed in the way the browser would be.

    pip install -r requirements.txt pytest -r ../bee-database/server/requirements.txt
    pytest -q tests
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BEE = Path(os.environ.get("BEE_SERVER_DIR", ROOT.parent / "bee-database" / "server"))
sys.path.insert(0, str(ROOT / "server"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def stack():
    data, port = tempfile.mkdtemp(), free_port()
    env = {**os.environ, "DATA_DIR": data, "PORT": str(port), "MCP_API_TOKEN": "svc-token", "MCP_PASSWORD": "ownerpw",
           "MCP_FAMILY_PASSWORD": "familypw", "PUBLIC_URL": f"http://127.0.0.1:{port}", "OWNER_EMAIL": "owner@example.com"}
    srv = subprocess.Popen([sys.executable, "app.py"], cwd=BEE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("bee-database didn't start: " + srv.stderr.read().decode()[-500:])
        c = sqlite3.connect(Path(data) / "mcp.db")
        c.execute("INSERT OR REPLACE INTO settings VALUES ('family_roles', ?)", (json.dumps({"nibble": "editor"}),))
        c.commit()
        os.environ.update(DB_URL=f"http://127.0.0.1:{port}", DB_TOKEN="svc-token", PUBLIC_URL=f"http://127.0.0.1:{port}")
        import app as nibble
        yield nibble, port
    finally:
        srv.terminate()
        srv.wait(10)


def signed_in(port: int, password: str) -> str:
    """The bee_session cookie value, from the database's own sign-in page."""
    import httpx2 as httpx

    r = httpx.post(f"http://127.0.0.1:{port}/auth/web/login", data={"password": password, "next": "/nibble"},
                   follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    return r.cookies["bee_session"]


@pytest.fixture(scope="module")
def clients(stack):
    from starlette.testclient import TestClient

    nibble, port = stack
    import asyncio
    asyncio.run(nibble.ensure_schema())

    def make(password):
        c = TestClient(nibble.app, base_url="http://localhost", follow_redirects=False)
        if password:
            c.cookies.set("bee_session", signed_in(port, password))
        return c
    return {"anon": make(None), "owner": make("ownerpw"), "family": make("familypw")}


RECIPE = {"title": "Soup", "category": "meals", "tags": ["Winter"],
          "components": [{"name": "Soup", "ingredients": [{"qty": "2", "item": "leeks"}], "steps": ["Chop the leeks."]}]}


def call(c, tool, **args):
    return c.post(f"/nibble/api/{tool}", json=args)


def test_signed_out_people_go_to_the_database_sign_in(clients):
    r = clients["anon"].get("/nibble")
    assert r.status_code == 303 and r.headers["location"] == "/auth/web/login?next=/nibble"
    assert call(clients["anon"], "recipe_list").status_code == 401


def test_page_is_nibble_and_knows_the_role(clients):
    r = clients["family"].get("/nibble")
    assert r.status_code == 200 and "<title>Nibble</title>" in r.text and "Moomin" not in r.text
    assert 'name="viewport" content="width=device-width, initial-scale=1' in r.text  # or phones render it zoomed out
    assert '"canEdit": true' in r.text and '"canDelete": false' in r.text
    assert '"canDelete": true' in clients["owner"].get("/nibble").text


def test_recipes_round_trip_through_the_database(clients, stack):
    fam = clients["family"]
    r = call(fam, "recipe_save", recipe=RECIPE).json()
    assert r["status"] == "draft" and r["tags"] == ["winter"]
    assert r["components"][0]["ingredients"] == [{"qty": 2.0, "item": "leeks"}]
    assert call(fam, "recipe_patch", id=r["id"], fields={"status": "live"}).json()["status"] == "live"
    assert [x["title"] for x in call(fam, "recipe_list", query="leek").json()["results"]] == ["Soup"]
    assert [x["title"] for x in call(fam, "recipe_list", category="meals", status="live").json()["results"]] == ["Soup"]
    assert call(fam, "recipe_list", category="baking").json()["count"] == 0
    assert call(fam, "recipe_get", id=r["id"]).json()["title"] == "Soup"
    # it is an ordinary record in the database, with readable content
    nibble, port = stack
    import asyncio
    rec = asyncio.run(nibble.db.call("get", id=r["id"]))
    assert rec["project"] == "nibble" and "leeks" in rec["content"]
    bad = call(fam, "recipe_save", recipe={"title": "x", "category": "soup"})
    assert bad.status_code == 400 and "category" in bad.json()["error"]["message"]


def test_roles_decide_who_can_delete(clients):
    fam, owner = clients["family"], clients["owner"]
    rid = call(fam, "recipe_save", recipe={**RECIPE, "title": "Temp"}).json()["id"]
    assert call(fam, "recipe_delete", id=rid).status_code == 403
    assert call(owner, "recipe_delete", id=rid).status_code == 200
    assert call(owner, "recipe_get", id=rid).status_code == 404


def test_foreign_origin_and_non_json_are_refused(clients, stack):
    fam = clients["family"]
    assert fam.post("/nibble/api/recipe_list", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert fam.post("/nibble/api/recipe_list", data="x=1").status_code == 415
    assert fam.post("/nibble/api/nope", json={}).status_code == 403


PASTED = """Lemon Drizzle Cake
A light sponge with a sharp lemon syrup.
Serves 8
Ingredients
225g unsalted butter, softened
2 x 400g tins chopped tomatoes
1 ½ tbsp milk
4 eggs
For the drizzle:
Juice of 1½ lemons
85g caster sugar
Method
1. Heat the oven to 180C.
2. Beat the butter and sugar until pale
and creamy.
3. Bake for 45 minutes.
"""


def test_pasted_text_becomes_a_draft_that_saves(clients):
    fam = clients["family"]
    r = call(fam, "recipe_parse_text", text=PASTED, source_url="https://example.com/cake")
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["title"] == "Lemon Drizzle Cake" and rec["servings"] == 8 and rec["status"] == "draft"
    main, drizzle = rec["components"]
    assert main["ingredients"][:4] == [
        {"qty": 225, "unit": "g", "item": "unsalted butter", "note": "softened"},
        {"qty": 2, "unit": "tin", "item": "chopped tomatoes", "note": "400 g"},
        {"qty": 1.5, "unit": "tbsp", "item": "milk"},
        {"qty": 4, "item": "eggs"},
    ]
    assert [s["text"] for s in main["steps"]] == [
        "Heat the oven to 180C.", "Beat the butter and sugar until pale and creamy.", "Bake for 45 minutes."]
    assert drizzle["name"] == "Drizzle" and drizzle["ingredients"][1]["qty"] == 85
    assert call(fam, "recipe_save", recipe=rec).status_code == 200


def test_pasted_text_without_headings_and_nonsense(clients):
    fam = clients["family"]
    rec = call(fam, "recipe_parse_text", text="Tomato pasta\n2 tbsp olive oil\n1 onion, chopped\nSalt\n"
               "Fry the onion in the oil for 5 minutes.\nStir in the pasta.").json()
    (c,) = rec["components"]
    assert [i["item"] for i in c["ingredients"]] == ["olive oil", "onion", "Salt"]
    assert [s["text"] for s in c["steps"]] == ["Fry the onion in the oil for 5 minutes.", "Stir in the pasta."]
    assert call(fam, "recipe_parse_text", text="   ").status_code == 400
    assert call(fam, "recipe_parse_text", text="just a title").status_code == 400


MODEL_RECIPE = {"title": "Cake", "category": "baking", "components": [{"name": "Sponge",
    "ingredients": [{"qty": 225, "unit": "g", "item": "butter"}],
    "steps": [{"text": "Bake for 45 minutes, until golden, then take it out.", "timer_seconds": 2700}]}]}


def test_a_model_converts_imports_when_set_up(clients, stack, monkeypatch):
    nibble, _ = stack
    fam = clients["family"]
    seen = []

    async def answer(prompt):
        seen.append(prompt)
        return "Here you go:\n```json\n" + json.dumps(MODEL_RECIPE) + "\n```", "some/free-model"

    monkeypatch.setattr(nibble.llm, "API_KEY", "k")
    monkeypatch.setattr(nibble.llm, "complete", answer)
    rec = call(fam, "recipe_parse_text", text=PASTED, source_url="https://example.com/x").json()
    assert rec["_import"] == {"by": "some/free-model"} and rec["status"] == "draft"
    assert rec["components"][0]["steps"][0]["timer_seconds"] == 2700 and rec["source_url"] == "https://example.com/x"
    assert "one action" in seen[0].lower() and "Lemon Drizzle Cake" in seen[0]

    async def page(url):
        return {"url": url, "title": "Cake page", "text": "words", "image": "https://example.com/i.jpg",
                "structured": {"name": "Cake", "ingredients": ["225g butter"], "instructions": [{"text": "Mix and bake."}]}}

    monkeypatch.setattr(nibble, "recipe_fetch_page", page)
    rec = call(fam, "recipe_import_page", url="https://example.com/cake").json()
    assert rec["_import"]["by"] == "some/free-model" and rec["image_url"] == "https://example.com/i.jpg"
    assert '"structured_data"' in seen[1]

    # a broken answer falls back to Nibble's own reading, and says why
    async def junk(prompt):
        return "Sorry, I can't help with that.", "some/free-model"

    monkeypatch.setattr(nibble.llm, "complete", junk)
    rec = call(fam, "recipe_parse_text", text=PASTED).json()
    assert rec["_import"]["by"] == "reader" and "no recipe" in rec["_import"]["why"]
    rec = call(fam, "recipe_import_page", url="https://example.com/cake").json()
    assert rec["_import"]["by"] == "page" and rec["components"][0]["ingredients"] == [{"qty": 225, "unit": "g", "item": "butter"}]
    assert call(fam, "recipe_save", recipe=rec).status_code == 200


def test_logout_goes_to_the_database(clients):
    r = clients["family"].post("/nibble/logout")
    assert r.status_code == 307 and r.headers["location"] == "/auth/web/logout"


SHOP_CASES = [
    ("2 x 400g tins chopped tomatoes, drained", "chopped tomatoes"),
    ("chopped tomatoes", "chopped tomatoes"),
    ("Salt", "salt"),
    ("Black pepper, to taste", "black pepper"),
    ("salt and pepper to taste", "salt and pepper"),
    ("3 garlic cloves, finely chopped", "garlic cloves"),
    ("1 onion finely chopped", "onion"),
    ("onion, chopped", "onion"),
    ("500 g skinless chicken thighs or breast", "skinless chicken thighs or breast"),
    ("1 ½ tsp ground cumin", "ground cumin"),
    ("a pinch of sea salt", "sea salt"),
    ("pinch of salt (optional)", "salt"),
    ("1/2 lemon, juice only", "lemon"),
    ("freshly ground black pepper", "black pepper"),
    ("", ""),
]


def test_shopping_search_term_is_the_ingredient_alone(tmp_path):
    """The page's shopTerm() (the block between the shopping-term markers) run under node."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node not installed")
    page = (ROOT / "server" / "static" / "nibble.html").read_text()
    block = page.split("/* shopping-term:start", 1)[1].split("/* shopping-term:end */", 1)[0]
    block = block.split("\n", 1)[1]
    script = tmp_path / "term.js"
    script.write_text(block + "\nconsole.log(JSON.stringify(JSON.parse(process.argv[2]).map(shopTerm)));\n")
    out = subprocess.run(["node", str(script), json.dumps([c[0] for c in SHOP_CASES])], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [c[1] for c in SHOP_CASES]
