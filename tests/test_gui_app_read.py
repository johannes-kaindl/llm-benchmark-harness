# tests/test_gui_app_read.py
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunRegistry


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


def _mk_judged(tmp_path):
    d = tmp_path / "2026-06-20_eval_ndassist"
    d.mkdir(parents=True)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "qwen2.5:3b", "quant": "q"}],
                "date": "2026-06-20",
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    return d


def test_overview_lists_bundles(tmp_path):
    _mk_judged(tmp_path)
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "ndassist" in r.text


def test_pack_explorer_renders_dimensions(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    # route reads the pack file by path param; ndassist has dimensions Q1..Q7
    assert r.status_code == 200
    assert "Q1" in r.text or "Sicherheit" in r.text


def test_compare_renders_table(tmp_path):
    _mk_judged(tmp_path)
    r = _client(tmp_path).get("/compare")
    assert r.status_code == 200
