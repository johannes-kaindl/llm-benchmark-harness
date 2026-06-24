# tests/test_gui_compare_route.py
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from test_gui_compare import (
    PACK,
    _resp,
    _two_model_bundle,
    _two_variant_bundle,
    _verdict,
    _write_compare_bundle,
)

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.judge import write_reports_jsonl
from touchstone.pack import load_pack
from touchstone.results import ModelReport


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
    assert "data-scatter" in body  # ① scatter
    assert "Prozentpunkte" in body  # relations fazit
    assert "n. v." in body  # CPU column empty
    # scatter data-points must be in single-quoted attribute so JSON strings don't break HTML
    import re

    m = re.search(r"data-points='([^']+)'", body)
    assert m is not None, "data-points attribute not found or not single-quoted"
    pts = json.loads(m.group(1))
    assert isinstance(pts, list) and len(pts) > 0
    assert "label" in pts[0]


def test_compare_route_renders_model_delta_row(tmp_path):
    d = tmp_path / "2026_eval_delta"
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    _write_compare_bundle(
        d,
        cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={("m", "baseline"): full, ("m", "none"): dict(full)},
        perf_by_cell={
            ("m", "baseline"): {"sys_used_mb": 52000.0, "sys_used_delta_mb": 12000.0},
            ("m", "none"): {"sys_used_mb": 50000.0, "sys_used_delta_mb": 10000.0},
        },
    )
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "Modell-Delta" in r.text  # the new ui.mlabel row header
    assert "11.7 GB" in r.text  # 12000 MB / 1024 ≈ 11.7 GB (baseline cell)


def test_compare_route_renders_thinking_row(tmp_path):
    # a reasoning model surfaces a Thinking row with the median duration + tok/s sub-line.
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    d = tmp_path / "2026_eval_think"
    d.mkdir(parents=True)
    pk = load_pack(PACK)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-20",
                "host": {"machine": "t"},
            }
        ),
        encoding="utf-8",
    )
    rb = _resp("m", "baseline", reasoning_duration_s=2.5, reasoning_tps=42.0)
    rn = _resp("m", "none", reasoning_duration_s=4.0, reasoning_tps=30.0)
    (d / "responses.jsonl").write_text(
        json.dumps(rb.as_dict()) + "\n" + json.dumps(rn.as_dict()) + "\n", encoding="utf-8"
    )
    write_reports_jsonl(
        d / "reports.jsonl",
        [ModelReport("m", "baseline", dict(full), {}), ModelReport("m", "none", dict(full), {})],
    )
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    (d / "judgements.jsonl").write_text("", encoding="utf-8")
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "Thinking-Dauer" in r.text  # the new ui.mlabel row header
    assert "2.50 s" in r.text  # baseline median duration
    assert "42 tok/s" in r.text  # baseline median reasoning tok/s sub-line


def test_compare_route_thinking_row_na_for_non_reasoning(tmp_path):
    # a non-reasoning model (zero timing) shows the row but with 'n. v.', no '0.00 s'.
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "Thinking-Dauer" in r.text  # row always present
    assert "0.00 s" not in r.text  # zero reasoning duration is not rendered as a time


def test_compare_route_model_axis_shows_projection(tmp_path):
    d = _two_model_bundle(tmp_path)
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=model")
    assert r.status_code == 200
    assert "Variante: baseline" in r.text  # projected variant labeled


def test_compare_route_bad_axis_422(tmp_path):
    d = _two_variant_bundle(tmp_path)
    assert _client(tmp_path).get(f"/compare/{d.name}?axis=bogus").status_code == 422


def test_compare_route_traversal_404(tmp_path):
    assert _client(tmp_path).get("/compare/..%2f..%2fetc").status_code == 404


def test_compare_route_single_axis_value_message(tmp_path):
    d = tmp_path / "2026_eval_one"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
        },
    )
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=model")
    assert r.status_code == 200
    assert "nichts zu vergleichen" in r.text.lower()


def test_compare_route_unjudged_perf_only(tmp_path):
    # un-judged: reports.jsonl absent + no scores.csv dims -> no quality, perf only
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-20",
                "host": {"machine": "t"},
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text(
        json.dumps(_resp("m", "baseline").as_dict())
        + "\n"
        + json.dumps(_resp("m", "none").as_dict())
        + "\n",
        encoding="utf-8",
    )
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "noch nicht bewertet" in r.text.lower()


def test_result_shows_compare_link_when_comparable(tmp_path):
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert r.status_code == 200
    assert f"/compare/{d.name}?axis=variant" in r.text
    assert "↔ Vergleichen" in r.text


def test_result_no_compare_link_when_single(tmp_path):
    d = tmp_path / "2026_eval_one"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
        },
    )
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert "↔ Vergleichen" not in r.text


def test_overview_shows_compare_link_for_multi_variant(tmp_path):
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert f"/compare/{d.name}?axis=variant" in r.text


# ── Review fixes (adversarial 3-perspective review) ───────────────────────────


def test_model_axis_no_dead_end_variant_switch(tmp_path):
    """§6 'Keine Sackgassen': a 2-model x 1-variant bundle must not offer a 'nach Variante'
    switch that lands on a single-cell 'nichts zu vergleichen' page."""
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    d = tmp_path / "2026_eval_2m1v"
    _write_compare_bundle(
        d,
        cells=[("alpha", "baseline"), ("beta", "baseline")],
        dim_scores_by_cell={("alpha", "baseline"): full, ("beta", "baseline"): dict(full)},
    )
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=model")
    assert r.status_code == 200
    assert "baseline" in r.text and "alpha" in r.text and "beta" in r.text
    assert "?axis=variant" not in r.text  # only 1 variant -> no dead-end switch


def test_model_axis_projection_label_not_doubled(tmp_path):
    """§4: the projection header reads 'Modelle bei Variante: baseline', not '… Variante: Variante: …'."""
    d = _two_model_bundle(tmp_path)
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=model")
    assert r.status_code == 200
    assert "Variante: Variante" not in r.text
    stripped = r.text.replace("<strong>", "").replace("</strong>", "")
    assert "Modelle bei Variante: baseline" in stripped


def test_drilldown_surfaces_empty_answer_marker(tmp_path):
    """§3 Phase-1 reuse: an empty/reasoning-only answer must be flagged in the drill-down."""
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    d = tmp_path / "2026_eval_empty"
    d.mkdir(parents=True)
    pk = load_pack(PACK)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-20",
                "host": {"machine": "t"},
            }
        ),
        encoding="utf-8",
    )
    rb = _resp("m", "baseline", prompt_id="A1", response_text="eine volle Antwort")
    rn = _resp(
        "m", "none", prompt_id="A1", response_text="", content_empty=True, reasoning_chars=120
    )
    (d / "responses.jsonl").write_text(
        json.dumps(rb.as_dict()) + "\n" + json.dumps(rn.as_dict()) + "\n", encoding="utf-8"
    )
    write_reports_jsonl(
        d / "reports.jsonl",
        [ModelReport("m", "baseline", dict(full), {}), ModelReport("m", "none", dict(full), {})],
    )
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    vb = _verdict("m", "baseline", "A1", 5)
    vn = _verdict("m", "none", "A1", 2)
    (d / "judgements.jsonl").write_text(
        json.dumps(vb.as_dict()) + "\n" + json.dumps(vn.as_dict()) + "\n", encoding="utf-8"
    )
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "leere Antwort" in r.text
    assert "nur Reasoning" in r.text
