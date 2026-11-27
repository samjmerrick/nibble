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


def test_logout_goes_to_the_database(clients):
    r = clients["family"].post("/nibble/logout")
    assert r.status_code == 307 and r.headers["location"] == "/auth/web/logout"
