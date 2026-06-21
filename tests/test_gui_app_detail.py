# tests/test_gui_app_detail.py
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunRegistry
from ramcheck.judge import write_reports_jsonl
from ramcheck.results import ModelReport


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


def _mk(tmp_path):
    d = tmp_path / "2026_eval_nd"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-21",
                "host": {},
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    write_reports_jsonl(
        d / "reports.jsonl",
        [ModelReport("m", "none", {"Q6": 2}, {"Q6": "schwach bei E1"})],
    )
    return d


def test_result_renders_rationale_and_ko(tmp_path):
    _mk(tmp_path)
    r = _client(tmp_path).get("/result/2026_eval_nd")
    assert r.status_code == 200
    # content assertions (rationale text) activated in Task 6 once templates render them


def test_pack_explainer_present(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    assert r.status_code == 200
    assert "holistisch" in r.text.lower()  # the method explainer (L9)
