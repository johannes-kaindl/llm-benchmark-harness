from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry

VALID = """
id: demo
title: Demo
scale: {1: a, 2: b, 3: c, 4: d, 5: e}
dimensions:
  - {id: Q1, name: Qualität, weight: 2}
ko_rule: {dimension: Q1, threshold: 2, red_flag_prompts: []}
prompt_variants:
  - {id: none, system_prompt: null}
categories:
  - id: A
    name: Allgemein
    prompts:
      - {id: A1, title: Erste, prompt: "Frage?"}
"""


class _FakeLauncher:
    def spawn(self, argv):
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


# ── GET /pack-editor ────────────────────────────────────────────────────────────


def test_pack_editor_loads_new_template(tmp_path):
    body = _client(tmp_path).get("/pack-editor").text
    assert "packEditor(" in body  # Alpine component bound
    assert "neues_pack" in body  # the NEW_PACK_TEMPLATE seeded into the textarea
    assert "/static/pack_editor.js" in body


def test_pack_editor_js_loads_non_deferred(tmp_path):
    # must register packEditor before the deferred Alpine fires alpine:init, else the editor is dead
    body = _client(tmp_path).get("/pack-editor").text
    assert '<script src="/static/pack_editor.js"></script>' in body
    assert 'defer src="/static/pack_editor.js"' not in body


def test_pack_editor_loads_existing_raw_yaml(tmp_path):
    body = _client(tmp_path).get("/pack-editor?path=packs/ndassist.yaml").text
    # raw YAML (not the rendered viewer) lands in the textarea — a yaml key proves it
    assert "dimensions:" in body
    assert "ndassist" in body


def test_pack_editor_rejects_traversal(tmp_path):
    assert _client(tmp_path).get("/pack-editor?path=../etc/passwd").status_code == 404


def test_pack_editor_rejects_unknown_pack(tmp_path):
    assert _client(tmp_path).get("/pack-editor?path=packs/nope.yaml").status_code == 404


def test_pack_editor_rejects_symlink_escape(tmp_path, monkeypatch):
    # a symlink inside packs/ that points OUTSIDE must not leak the target (defense in depth)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "packs").mkdir()
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    (tmp_path / "packs" / "evil.yaml").symlink_to(tmp_path / "secret.txt")
    r = _client(tmp_path).get("/pack-editor?path=packs/evil.yaml")
    assert r.status_code == 404
    assert "top secret" not in r.text


def test_validate_preview_escapes_user_html(tmp_path):
    # preview_html is consumed via x-html; user pack fields MUST be entity-escaped (no XSS)
    evil = VALID.replace("name: Qualität", "name: '<img src=x onerror=alert(1)>'")
    r = _client(tmp_path).post("/packs/validate", data={"yaml_text": evil})
    j = r.json()
    assert j["ok"] is True
    assert "<img src=x onerror" not in j["preview_html"]  # never a live tag
    assert "&lt;img src=x onerror" in j["preview_html"]  # rendered as inert text


# ── POST /packs/validate ────────────────────────────────────────────────────────


def test_validate_ok_returns_preview(tmp_path):
    r = _client(tmp_path).post("/packs/validate", data={"yaml_text": VALID})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["errors"] == []
    assert j["summary"]["prompts"] == 1
    assert "Dimensionen" in j["preview_html"]  # the reused viewer body


def test_validate_invalid_returns_errors_no_preview(tmp_path):
    bad = VALID.replace("dimension: Q1", "dimension: Q9")  # dangling ko dimension
    r = _client(tmp_path).post("/packs/validate", data={"yaml_text": bad})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False
    assert j["errors"]
    assert j["preview_html"] is None


def test_validate_never_500_on_garbage(tmp_path):
    r = _client(tmp_path).post("/packs/validate", data={"yaml_text": "id: [unterminated\n"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


# ── POST /packs/save ────────────────────────────────────────────────────────────


def test_save_writes_valid_new_pack(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "packs").mkdir()
    r = _client(tmp_path).post("/packs/save", data={"filename": "demo", "yaml_text": VALID})
    assert r.status_code == 200
    assert r.json()["saved"] == "demo.yaml"
    assert (tmp_path / "packs" / "demo.yaml").exists()


def test_save_rejects_invalid_yaml_no_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "packs").mkdir()
    bad = VALID.replace("dimension: Q1", "dimension: Q9")
    r = _client(tmp_path).post("/packs/save", data={"filename": "demo", "yaml_text": bad})
    assert r.status_code == 400
    assert not (tmp_path / "packs" / "demo.yaml").exists()


def test_save_rejects_bad_filename_no_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "packs").mkdir()
    r = _client(tmp_path).post("/packs/save", data={"filename": "../evil", "yaml_text": VALID})
    assert r.status_code == 400
    assert list((tmp_path / "packs").iterdir()) == []


def test_save_conflict_then_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs" / "demo.yaml").write_text("old", encoding="utf-8")
    client = _client(tmp_path)
    # existing file, no overwrite flag → 409, original untouched
    r = client.post("/packs/save", data={"filename": "demo", "yaml_text": VALID})
    assert r.status_code == 409
    assert (tmp_path / "packs" / "demo.yaml").read_text(encoding="utf-8") == "old"
    # with overwrite → 200, replaced
    r2 = client.post(
        "/packs/save", data={"filename": "demo", "yaml_text": VALID, "overwrite": "true"}
    )
    assert r2.status_code == 200
    assert "id: demo" in (tmp_path / "packs" / "demo.yaml").read_text(encoding="utf-8")


def test_save_confined_to_packs_dir(tmp_path, monkeypatch):
    # even a filename that passes the regex must resolve inside packs/ (defense in depth)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "packs").mkdir()
    r = _client(tmp_path).post("/packs/save", data={"filename": "ok_name", "yaml_text": VALID})
    assert r.status_code == 200
    written = Path("packs") / "ok_name.yaml"
    assert written.resolve().is_relative_to((tmp_path / "packs").resolve())
