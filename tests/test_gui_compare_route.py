# tests/test_gui_compare_route.py
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from test_aggregate import _write_scores_pool
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
    # Content now lives at /result; assert it renders correctly there.
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get(f"/result/{d.name}?axis=variant")
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
    # Content now lives at /result; assert it renders correctly there.
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
    r = _client(tmp_path).get(f"/result/{d.name}?axis=variant")
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
    r = _client(tmp_path).get(f"/result/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "Thinking-Dauer" in r.text  # the new ui.mlabel row header
    assert "2.50 s" in r.text  # baseline median duration
    assert "42 tok/s" in r.text  # baseline median reasoning tok/s sub-line


def test_compare_route_thinking_row_na_for_non_reasoning(tmp_path):
    # a non-reasoning model (zero timing) shows the row but with 'n. v.', no '0.00 s'.
    # Content now lives at /result.
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get(f"/result/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "Thinking-Dauer" in r.text  # row always present
    assert "0.00 s" not in r.text  # zero reasoning duration is not rendered as a time


def test_compare_route_model_axis_shows_projection(tmp_path):
    # Content now lives at /result.
    d = _two_model_bundle(tmp_path)
    r = _client(tmp_path).get(f"/result/{d.name}?axis=model")
    assert r.status_code == 200
    assert "Variante: baseline" in r.text  # projected variant labeled


def test_result_route_bad_axis_422(tmp_path):
    # The axis-validation guard lives on /result; assert it directly.
    d = _two_variant_bundle(tmp_path)
    assert _client(tmp_path).get(f"/result/{d.name}?axis=bogus").status_code == 422


def test_result_route_traversal_404(tmp_path):
    # The path-traversal guard lives on /result; assert it directly.
    assert _client(tmp_path).get("/result/..%2f..%2fetc").status_code == 404


def test_compare_route_single_axis_value_message(tmp_path):
    # Content now lives at /result.
    d = tmp_path / "2026_eval_one"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
        },
    )
    r = _client(tmp_path).get(f"/result/{d.name}?axis=model")
    assert r.status_code == 200
    assert "nichts zu vergleichen" in r.text.lower()


def test_compare_route_unjudged_perf_only(tmp_path):
    # un-judged: reports.jsonl absent + no scores.csv dims -> no quality, perf only.
    # Content now lives at /result.
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
    r = _client(tmp_path).get(f"/result/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "noch nicht bewertet" in r.text.lower()


def test_result_shows_compare_link_when_comparable(tmp_path):
    """Task 4: comparable bundles show the inline comparison block, not the old /compare/ button."""
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert r.status_code == 200
    # Inline comparison block replaces the old ↔ Vergleichen button
    assert "Kopf-an-Kopf" in r.text
    assert "Effizienz-Relation" in r.text
    # No dead /compare/<name> links (the sidebar /compare link is ok but within-bundle not)
    assert f"/compare/{d.name}" not in r.text


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
    assert f"/result/{d.name}?axis=variant" in r.text
    assert f"/compare/{d.name}" not in r.text


# ── Review fixes (adversarial 3-perspective review) ───────────────────────────


def test_model_axis_no_dead_end_variant_switch(tmp_path):
    """§6 'Keine Sackgassen': a 2-model x 1-variant bundle must not offer a 'nach Variante'
    switch that lands on a single-cell 'nichts zu vergleichen' page.
    Content now lives at /result."""
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    d = tmp_path / "2026_eval_2m1v"
    _write_compare_bundle(
        d,
        cells=[("alpha", "baseline"), ("beta", "baseline")],
        dim_scores_by_cell={("alpha", "baseline"): full, ("beta", "baseline"): dict(full)},
    )
    r = _client(tmp_path).get(f"/result/{d.name}?axis=model")
    assert r.status_code == 200
    assert "baseline" in r.text and "alpha" in r.text and "beta" in r.text
    assert "?axis=variant" not in r.text  # only 1 variant -> no dead-end switch


def test_model_axis_projection_label_not_doubled(tmp_path):
    """§4: the projection header reads 'Modelle bei Variante: baseline', not '… Variante: Variante: …'.
    Content now lives at /result."""
    d = _two_model_bundle(tmp_path)
    r = _client(tmp_path).get(f"/result/{d.name}?axis=model")
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
    r = _client(tmp_path).get(f"/result/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "leere Antwort" in r.text
    assert "nur Reasoning" in r.text


# ── Task 5: redirect + method-explainer dedup ─────────────────────────────────


def test_compare_name_redirects_to_result(tmp_path):
    """GET /compare/{name}?… must 301-redirect to /result/{name}?… (same query string)."""
    d = _two_variant_bundle(tmp_path)
    resp = _client(tmp_path).get(f"/compare/{d.name}?axis=variant", follow_redirects=False)
    assert resp.status_code == 301
    loc = resp.headers["location"]
    assert loc.startswith(f"/result/{d.name}")
    assert "axis=variant" in loc


def test_method_explainer_rendered_exactly_once_on_comparable_bundle(tmp_path):
    """Part C: _method_explainer must appear exactly once on a comparable bundle.

    'Bewertungs-Methode' is a stable heading unique to the explainer block.
    """
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get(f"/result/{d.name}?axis=variant")
    assert r.status_code == 200
    # The explainer card title is unique to _method_explainer.html
    assert r.text.count("Bewertungs-Methode") == 1


# ── Task 3 (B2): /compare takes ?rows= → pool + auto-diff context ─────────────

_POOL_DIM = {
    "chip": "M2",
    "ram_gb": "16",
    "pack": "ndassist",
    "pack_version": "1",
    "model": "gemma",
    "quant": "Q4",
    "variant": "baseline",
    "ttft_p50": "0.2",
    "decode_med": "20",
    "peak_ram_gb": "8",
    "model_delta_gb": "4",
    "power": "ac",
    "metric_type": "dimension",
    "metric": "D1",
    "weight": "1",
    "score": "4",
}


def test_compare_pool_and_diff(tmp_path, monkeypatch):
    """GET /compare?rows=id1,id2 → pool contains both rows, diff is not None with 2 columns."""
    run_a = "2026-01-01_000000_eval_ndassist"
    run_b = "2026-01-02_000000_eval_ndassist"
    _write_scores_pool(tmp_path / run_a, [_POOL_DIM])
    _write_scores_pool(tmp_path / run_b, [_POOL_DIM])

    seen: dict = {}
    real = gui_app.render

    def spy_render(template: str, request, **ctx):  # type: ignore[override]
        seen.update(ctx)
        return real(template, request, **ctx)

    monkeypatch.setattr(gui_app, "render", spy_render)

    client = _client(tmp_path)
    id_a = f"{run_a}|gemma|baseline"
    id_b = f"{run_b}|gemma|baseline"
    resp = client.get(f"/compare?rows={id_a},{id_b}")
    assert resp.status_code == 200
    assert "pool" in seen
    pool_ids = {r.id for r in seen["pool"]}
    assert id_a in pool_ids and id_b in pool_ids
    assert seen.get("diff") is not None
    assert len(seen["diff"].columns) == 2


def test_compare_no_rows_shows_pool_only(tmp_path, monkeypatch):
    """GET /compare without ?rows → pool is loaded, diff is None."""
    run_a = "2026-01-01_000000_eval_ndassist"
    _write_scores_pool(tmp_path / run_a, [_POOL_DIM])

    seen: dict = {}
    real = gui_app.render

    def spy_render(template: str, request, **ctx):  # type: ignore[override]
        seen.update(ctx)
        return real(template, request, **ctx)

    monkeypatch.setattr(gui_app, "render", spy_render)

    client = _client(tmp_path)
    resp = client.get("/compare")
    assert resp.status_code == 200
    assert "pool" in seen
    assert seen.get("diff") is None


def test_compare_bad_rows_ignored(tmp_path, monkeypatch):
    """GET /compare?rows=does|not|exist → 200, no crash, diff is None."""
    seen: dict = {}
    real = gui_app.render

    def spy_render(template: str, request, **ctx):  # type: ignore[override]
        seen.update(ctx)
        return real(template, request, **ctx)

    monkeypatch.setattr(gui_app, "render", spy_render)

    client = _client(tmp_path)
    resp = client.get("/compare?rows=does%7Cnot%7Cexist")
    assert resp.status_code == 200
    assert seen.get("diff") is None


# ── Task 4: compare.html selection pool (checkboxes + filters + compare button) ─


def test_compare_pool_has_checkboxes_and_compare_button(tmp_path):
    """GET /compare with ≥1 pool row → body contains checkbox, Alpine x-data, Vergleichen button,
    and a checkbox value fragment containing '|gemma|baseline'."""
    run_a = "2026-01-01_000000_eval_ndassist"
    _write_scores_pool(tmp_path / run_a, [_POOL_DIM])

    client = _client(tmp_path)
    resp = client.get("/compare")
    assert resp.status_code == 200
    body = resp.text
    assert 'type="checkbox"' in body
    assert "x-data" in body
    assert "Vergleichen" in body
    assert "|gemma|baseline" in body


# ── Task 5: auto-diff comparison section (GEMEINSAM + varying columns + winners) ─


def test_compare_diff_renders_common_and_columns(tmp_path):
    """Cross-machine comparison: two rows differing only in chip/ram_gb.

    The diff section must render GEMEINSAM (with the constant model), both chip
    values as column headers, and a trophy on the differing metric.
    """
    run_a = "2026-01-01_000000_eval_ndassist"
    run_b = "2026-01-02_000000_eval_ndassist"
    dim_a = dict(_POOL_DIM, chip="M1", ram_gb="8", peak_ram_gb="6", decode_med="15")
    dim_b = dict(_POOL_DIM, chip="M5", ram_gb="64", peak_ram_gb="10", decode_med="40")
    _write_scores_pool(tmp_path / run_a, [dim_a])
    _write_scores_pool(tmp_path / run_b, [dim_b])

    client = _client(tmp_path)
    id_a = f"{run_a}|gemma|baseline"
    id_b = f"{run_b}|gemma|baseline"
    resp = client.get(f"/compare?rows={id_a},{id_b}")
    assert resp.status_code == 200
    body = resp.text

    assert "GEMEINSAM" in body          # common block present
    assert "gemma" in body              # constant model shown in GEMEINSAM
    assert "M1" in body                 # varying chip A as column header
    assert "M5" in body                 # varying chip B as column header
    assert "🏆" in body                 # winner marker on at least one differing metric


# ── Task 6: import-bundle upload control ──────────────────────────────────────


def test_compare_has_import_control(tmp_path):
    """GET /compare → pool zone contains a file upload control targeting /import-bundle."""
    client = _client(tmp_path)
    resp = client.get("/compare")
    assert resp.status_code == 200
    body = resp.text
    assert 'type="file"' in body
    assert "/import-bundle" in body
    assert "Lauf importieren" in body
