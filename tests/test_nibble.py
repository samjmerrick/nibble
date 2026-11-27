"""Tests for the Nibble plugin, run against the bee-database server code on a fresh database.

    pip install -r ../bee-database/server/requirements.txt pytest
    pytest -q tests                      # BEE_SERVER_DIR defaults to ../bee-database/server
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BEE = Path(os.environ.get("BEE_SERVER_DIR", ROOT.parent / "bee-database" / "server"))
os.environ.update(DATA_DIR=tempfile.mkdtemp(), MCP_PASSWORD="ownerpw", PUBLIC_URL="http://localhost:8000",
                  OWNER_EMAIL="owner@example.com", PLUGIN_PATH=str(ROOT))
os.environ.pop("NIBBLE_URL", None)
os.environ.pop("RECIPES_URL", None)
sys.path.insert(0, str(BEE))

import access  # noqa: E402
import app  # noqa: E402

db = app.db


def as_user(email):
    return access.perms_for(db, f"user:{email}", app.OWNER_EMAIL)


def run(perms, tool_name, **args):
    tok = app._web_perms.set(perms)
    try:
        return app.TOOLS[tool_name].fn(**args)
    finally:
        app._web_perms.reset(tok)


OWNER = as_user("owner@example.com")
RECIPE = {"title": "Soup", "category": "meals", "tags": ["Winter"],
          "components": [{"name": "Soup", "ingredients": [{"qty": "2", "item": "leeks"}], "steps": ["Chop the leeks."]}]}


@pytest.fixture(scope="module", autouse=True)
def people():
    run(OWNER, "member_set", project="recipes", email="cook@example.com", role="editor")
    run(OWNER, "member_set", project="recipes", email="reader@example.com", role="viewer")


def test_plugin_loads_and_serves_the_web_app():
    assert "nibble" in app.plugins.available()
    (web_app,) = [a for a in app._host.web_apps if a.plugin == "nibble"]
    assert web_app.title == "Nibble" and web_app.path == "/recipes"
    page = web_app.page.read_text()
    assert "<title>Nibble</title>" in page and "Moomin" not in page


def test_tools_follow_roles():
    cook, reader = as_user("cook@example.com"), as_user("reader@example.com")
    assert app.tool_allowed(cook, "recipe_save") and not app.tool_allowed(cook, "recipe_delete")
    assert app.tool_allowed(reader, "recipe_list") and not app.tool_allowed(reader, "recipe_save")
    assert app.tool_allowed(OWNER, "recipe_delete")


def test_recipes_round_trip():
    cook = as_user("cook@example.com")
    r = run(cook, "recipe_save", recipe=RECIPE)
    assert r["status"] == "draft" and r["tags"] == ["winter"]
    assert r["components"][0]["ingredients"] == [{"qty": 2.0, "item": "leeks"}]
    assert run(cook, "recipe_patch", id=r["id"], fields={"status": "live"})["status"] == "live"
    assert [x["title"] for x in run(cook, "recipe_list", query="leek")["results"]] == ["Soup"]
    assert "leeks" in run(OWNER, "get", id=r["id"])["content"]
    with pytest.raises(app.ToolError, match="category"):
        run(cook, "recipe_save", recipe={"title": "x", "category": "soup"})


def test_takes_over_the_old_recipes_plugin_without_touching_data():
    """A database from before the split has project and collection owned by plugin 'recipes'."""
    import nibble
    from plugins import PluginContext

    rid = run(as_user("cook@example.com"), "recipe_save", recipe=RECIPE)["id"]
    db.execute("UPDATE projects SET plugin = 'recipes' WHERE slug = 'recipes'")
    db.execute("UPDATE collections SET plugin = 'recipes' WHERE project = 'recipes'")
    db.commit()
    nibble.setup(PluginContext("nibble", app._host))
    assert db.execute("SELECT plugin FROM projects WHERE slug = 'recipes'").fetchone()[0] == "nibble"
    assert {r[0] for r in db.execute("SELECT plugin FROM collections WHERE project = 'recipes'")} == {"nibble"}
    assert run(OWNER, "recipe_get", id=rid)["title"] == "Soup"
    with pytest.raises(app.ToolError, match="plugin"):
        run(OWNER, "project_set", project="recipes", guide="mine now")
