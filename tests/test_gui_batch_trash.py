from __future__ import annotations

import io
import os
import zipfile

from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui import trash
from touchstone.gui.control import RunRegistry, write_sentinel


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


def _mkrun(runs, name, *, bundle=True):
    d = runs / name
    d.mkdir(parents=True)
    if bundle:
        (d / "bundle.json").write_text('{"pack_id":"p"}', encoding="utf-8")
        (d / "responses.jsonl").write_text("{}\n", encoding="utf-8")
    return d


# ── batch-delete → move to .trash ────────────────────────────────────────────


def test_batch_delete_moves_selected_to_trash(tmp_path):
    _mkrun(tmp_path, "run_a")
    _mkrun(tmp_path, "run_b")
    _mkrun(tmp_path, "run_c")
    r = _client(tmp_path).post(
        "/runs/batch-delete", data={"names": ["run_a", "run_c"]}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert not (tmp_path / "run_a").exists()
    assert (tmp_path / ".trash" / "run_a").exists()
    assert (tmp_path / "run_b").exists()  # untouched
    assert trash.list_trash(tmp_path) == ["run_a", "run_c"]


def test_batch_delete_skips_active_run(tmp_path):
    _mkrun(tmp_path, "run_live")
    # a sentinel with THIS process's pid is genuinely active → must NOT be trashed
    write_sentinel(
        tmp_path / "run_live", kind="eval", pid=os.getpid(), pack_path="p", config_path="c"
    )
    r = _client(tmp_path).post(
        "/runs/batch-delete", data={"names": ["run_live"]}, follow_redirects=False
    )
    assert r.status_code == 303
    assert (tmp_path / "run_live").exists()  # still there
    assert trash.list_trash(tmp_path) == []


def test_batch_delete_empty_is_400(tmp_path):
    r = _client(tmp_path).post("/runs/batch-delete", data={"names": []})
    assert r.status_code == 400


def test_batch_delete_rejects_traversal(tmp_path):
    r = _client(tmp_path).post("/runs/batch-delete", data={"names": ["../../etc"]})
    assert r.status_code == 404


# ── batch-export → one zip ───────────────────────────────────────────────────


def test_batch_export_zips_selected_runs(tmp_path):
    _mkrun(tmp_path, "run_a")
    _mkrun(tmp_path, "run_b")
    _mkrun(tmp_path, "run_c")
    r = _client(tmp_path).post("/runs/batch-export", data={"names": ["run_a", "run_b"]})
    assert r.status_code == 200
    assert "zip" in r.headers["content-type"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "run_a/bundle.json" in names
    assert "run_b/responses.jsonl" in names
    assert not any(n.startswith("run_c/") for n in names)  # not selected


def test_batch_export_empty_is_400(tmp_path):
    assert _client(tmp_path).post("/runs/batch-export", data={"names": []}).status_code == 400


# ── trash view + restore + purge ─────────────────────────────────────────────


def test_trash_view_lists_trashed(tmp_path):
    trash.move_to_trash(_mkrun(tmp_path, "old_run"), tmp_path)
    body = _client(tmp_path).get("/trash").text
    assert "old_run" in body
    assert 'action="/trash/restore"' in body
    assert 'action="/trash/purge"' in body


def test_trash_restore_moves_back(tmp_path):
    trash.move_to_trash(_mkrun(tmp_path, "old_run"), tmp_path)
    r = _client(tmp_path).post("/trash/restore", data={"name": "old_run"}, follow_redirects=False)
    assert r.status_code == 303
    assert (tmp_path / "old_run").exists()
    assert trash.list_trash(tmp_path) == []


def test_trash_purge_clears_only_trash(tmp_path):
    live = _mkrun(tmp_path, "live_run")  # a real run in runs/
    trash.move_to_trash(_mkrun(tmp_path, "junk"), tmp_path)
    r = _client(tmp_path).post("/trash/purge", follow_redirects=False)
    assert r.status_code == 303
    assert trash.list_trash(tmp_path) == []  # trash emptied
    assert not (tmp_path / ".trash").exists() or trash.list_trash(tmp_path) == []
    assert live.exists()  # the real run is untouched


def test_trash_restore_rejects_traversal(tmp_path):
    (tmp_path / ".trash").mkdir()
    assert _client(tmp_path).post("/trash/restore", data={"name": "../../etc"}).status_code == 404


# ── overview markers ─────────────────────────────────────────────────────────


def test_overview_has_batch_selection_ui(tmp_path):
    _mkrun(tmp_path, "run_a")
    body = _client(tmp_path).get("/").text
    assert 'x-model="sel"' in body  # per-row checkbox
    assert 'action="/runs/batch-export"' in body
    assert 'action="/runs/batch-delete"' in body


def test_overview_trash_indicator_only_when_nonempty(tmp_path):
    _mkrun(tmp_path, "run_a")
    assert "Papierkorb:" not in _client(tmp_path).get("/").text  # empty → no indicator
    trash.move_to_trash(_mkrun(tmp_path, "junk"), tmp_path)
    body = _client(tmp_path).get("/").text
    assert "Papierkorb:" in body and 'href="/trash"' in body
