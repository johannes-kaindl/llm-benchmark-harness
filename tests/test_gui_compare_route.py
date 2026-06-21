# tests/test_gui_compare_route.py
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from test_gui_compare import _resp, _two_model_bundle, _two_variant_bundle, _write_compare_bundle

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


def test_compare_route_variant_axis_renders(tmp_path):
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=variant")
    assert r.status_code == 200
    body = r.text
    assert "baseline" in body and "none" in body
    assert "data-scatter" in body            # ① scatter
    assert "Prozentpunkte" in body           # relations fazit
    assert "n. v." in body                   # CPU column empty


def test_compare_route_model_axis_shows_projection(tmp_path):
    d = _two_model_bundle(tmp_path)
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=model")
    assert r.status_code == 200
    assert "Variante: baseline" in r.text     # projected variant labeled


def test_compare_route_bad_axis_422(tmp_path):
    d = _two_variant_bundle(tmp_path)
    assert _client(tmp_path).get(f"/compare/{d.name}?axis=bogus").status_code == 422


def test_compare_route_traversal_404(tmp_path):
    assert _client(tmp_path).get("/compare/..%2f..%2fetc").status_code == 404


def test_compare_route_single_axis_value_message(tmp_path):
    d = tmp_path / "2026_eval_one"
    _write_compare_bundle(
        d, cells=[("m", "baseline")],
        dim_scores_by_cell={("m", "baseline"): {q: 4 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]}},
    )
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=model")
    assert r.status_code == 200
    assert "nichts zu vergleichen" in r.text.lower()


def test_compare_route_unjudged_perf_only(tmp_path):
    # un-judged: reports.jsonl absent + no scores.csv dims -> no quality, perf only
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    (d / "bundle.json").write_text(json.dumps({
        "pack_id": "ndassist", "pack_path": "packs/ndassist.yaml",
        "models": [{"id": "m", "quant": "q"}], "date": "2026-06-20", "host": {"machine": "t"},
    }), encoding="utf-8")
    (d / "responses.jsonl").write_text(
        json.dumps(_resp("m", "baseline").as_dict()) + "\n"
        + json.dumps(_resp("m", "none").as_dict()) + "\n",
        encoding="utf-8",
    )
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "noch nicht bewertet" in r.text.lower()
