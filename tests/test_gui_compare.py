from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from touchstone.gui import compare
from touchstone.judge import write_reports_jsonl
from touchstone.models import ResourceSample
from touchstone.pack import load_pack
from touchstone.results import EvalResponse, ModelReport, Verdict

PACK = "packs/ndassist.yaml"


def _resp(model: str, variant: str, **over) -> EvalResponse:
    base = dict(
        pack_id="ndassist",
        pack_version=1,
        machine="t",
        model=model,
        quant="q",
        engine="e",
        engine_version="x",
        variant=variant,
        category="A",
        prompt_id="A1",
        repeat=0,
        response_text="ok",
        content_empty=False,
        ttft_s=0.1,
        decode_tps=10.0,
        prefill_tps=1.0,
        e2e_s=1.0,
        prompt_tokens=1,
        completion_tokens=1,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=0.0,
        sys_used_mb=8000.0,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.0,
        reasoning_chars=0,
    )
    base.update(over)
    return EvalResponse(**base)


def _sample(ts: float, cpu: float | None) -> ResourceSample:
    return ResourceSample(
        ts=ts,
        sys_used_mb=1.0,
        sys_available_mb=1.0,
        swap_used_mb=0.0,
        server_rss_mb=None,
        mem_pressure_level="normal",
        throttled=False,
        cpu_pct=cpu,
    )


def _write_compare_bundle(
    d,
    *,
    cells,  # list[(model, variant)]
    dim_scores_by_cell,  # {(model,variant): {dim_id: int}}
    perf_by_cell=None,  # {(model,variant): {"decode_tps","ttft_s","e2e_s","sys_used_mb"}}
    verdicts_by_cell=None,  # {(model,variant): list[Verdict]}
    rationales_by_cell=None,
    samples=None,  # list[ResourceSample] | None  (resources.jsonl)
):
    """Self-contained judged bundle using the real in-repo pack."""
    d.mkdir(parents=True, exist_ok=True)
    pk = load_pack(PACK)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "models": [{"id": m, "quant": "q"} for m, _ in cells],
                "date": "2026-06-20",
                "host": {"machine": "t"},
            }
        ),
        encoding="utf-8",
    )
    # responses.jsonl: one OK non-cold response per cell (A1), perf overridable
    perf_by_cell = perf_by_cell or {}
    resp_lines = []
    for m, v in cells:
        p = perf_by_cell.get((m, v), {})
        resp_lines.append(
            json.dumps(
                _resp(
                    m,
                    v,
                    decode_tps=p.get("decode_tps", 10.0),
                    ttft_s=p.get("ttft_s", 0.1),
                    e2e_s=p.get("e2e_s", 1.0),
                    sys_used_mb=p.get("sys_used_mb", 8000.0),
                    sys_used_delta_mb=p.get("sys_used_delta_mb"),
                ).as_dict()
            )
        )
    (d / "responses.jsonl").write_text("\n".join(resp_lines) + "\n", encoding="utf-8")
    # reports.jsonl: holistic dim_scores (+ optional rationales)
    rationales_by_cell = rationales_by_cell or {}
    reports = [
        ModelReport(
            m, v, dict(dim_scores_by_cell[(m, v)]), dict(rationales_by_cell.get((m, v), {}))
        )
        for m, v in cells
    ]
    write_reports_jsonl(d / "reports.jsonl", reports)
    # scores.csv stub so classify() reads 'judged' (bundle_detail prefers reports.jsonl)
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    # judgements.jsonl (optional, per-prompt verdicts for divergence)
    verdicts_by_cell = verdicts_by_cell or {}
    vlines = [json.dumps(v.as_dict()) for vs in verdicts_by_cell.values() for v in vs]
    (d / "judgements.jsonl").write_text(
        ("\n".join(vlines) + "\n") if vlines else "", encoding="utf-8"
    )
    if samples is not None:
        (d / "resources.jsonl").write_text(
            "\n".join(json.dumps(s.__dict__) for s in samples) + "\n", encoding="utf-8"
        )
    return pk


def _two_variant_bundle(tmp_path, **kw):
    """ndassist 1 model × {baseline, none}. baseline strong, none Q6=2 (K.-o.)."""
    d = tmp_path / "2026_eval_cmp"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
            ("m", "none"): {q: 2 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
        },
        perf_by_cell={
            ("m", "baseline"): {
                "decode_tps": 12.0,
                "ttft_s": 0.20,
                "e2e_s": 1.5,
                "sys_used_mb": 8200.0,
            },
            ("m", "none"): {
                "decode_tps": 14.0,
                "ttft_s": 0.15,
                "e2e_s": 1.2,
                "sys_used_mb": 8000.0,
            },
        },
        **kw,
    )
    return d


def _mk_runs(p):
    return p


def test_axis_options_multi_variant_single_model():
    responses = [_resp("m", "baseline"), _resp("m", "none")]
    opts = compare.axis_options(responses)
    assert opts.models == ["m"]
    assert opts.variants == ["baseline", "none"]
    assert opts.default_axis == "variant"
    assert opts.default_label == "Variante"
    assert opts.comparable is True


def test_axis_options_multi_model_defaults_to_model_axis():
    responses = [_resp("a", "baseline"), _resp("b", "baseline")]
    opts = compare.axis_options(responses)
    assert opts.default_axis == "model"
    assert opts.default_label == "Modell"
    assert opts.comparable is True


def test_axis_options_single_everything_not_comparable():
    opts = compare.axis_options([_resp("m", "baseline")])
    assert opts.comparable is False


def test_cpu_for_window_none_ticks_yield_na():
    # the real state today: ticks exist but carry no cpu_pct -> "n. v." (None, None)
    responses = [_resp("m", "baseline", t_start=0.0, t_end=2.0)]
    samples = [_sample(0.5, None), _sample(1.5, None)]
    assert compare._cpu_for_window(samples, responses) == (None, None)


def test_cpu_for_window_max_and_avg():
    responses = [_resp("m", "baseline", t_start=0.0, t_end=2.0)]
    samples = [_sample(0.5, 40.0), _sample(1.5, 60.0), _sample(9.9, 100.0)]  # last outside window
    cpu_max, cpu_avg = compare._cpu_for_window(samples, responses)
    assert cpu_max == 60.0
    assert cpu_avg == 50.0


def test_cpu_for_window_empty_samples():
    assert compare._cpu_for_window([], [_resp("m", "baseline")]) == (None, None)


def test_cell_metrics_reasoning_medians_over_ok_responses():
    # reasoning duration/tps medians come from ok, non-cold responses (nan-safe like the others).
    responses = [
        _resp("m", "baseline", reasoning_duration_s=2.0, reasoning_tps=30.0),
        _resp("m", "baseline", reasoning_duration_s=4.0, reasoning_tps=50.0),
        _resp("m", "baseline", is_cold_start=True, reasoning_duration_s=99.0, reasoning_tps=1.0),
    ]
    cell = compare._cell_metrics("baseline", "m", "baseline", responses, None, {}, {}, [])
    assert cell.reasoning_duration_med == 3.0  # median(2,4); cold-start excluded
    assert cell.reasoning_tps_med == 40.0  # median(30,50)


def test_cell_metrics_reasoning_medians_none_when_absent():
    # all-zero reasoning (non-reasoning model) → no median surfaced.
    responses = [_resp("m", "baseline"), _resp("m", "baseline")]
    cell = compare._cell_metrics("baseline", "m", "baseline", responses, None, {}, {}, [])
    assert cell.reasoning_duration_med is None
    assert cell.reasoning_tps_med is None


def test_compare_detail_variant_axis_quality_and_speed():
    d = _two_variant_bundle(pathlib.Path(_mk_runs(tempfile.mkdtemp())))
    detail = compare.compare_detail(d, "variant")
    assert detail is not None
    assert detail.axis == "variant"
    assert detail.single is False
    labels = [c.label for c in detail.cells]
    assert labels == ["baseline", "none"]
    base = next(c for c in detail.cells if c.label == "baseline")
    none = next(c for c in detail.cells if c.label == "none")
    # quality from master_rows ⨝ reports: baseline 4*16/80*... -> 80%, none -> 40%
    assert round(base.pct) == 80
    assert round(none.pct) == 40
    assert base.rubric_level == "solide"  # 80% -> solide
    assert none.rubric_level == "ungenügend"  # 40% -> ungenügend
    assert none.safety_passed is False
    # speed/RAM computed directly from EvalResponse
    assert base.decode_tps == 12.0
    assert base.peak_ram_mb == 8200.0
    assert none.peak_ram_mb == 8000.0
    # dim_scores come from the parallel report, not master_rows
    assert base.dim_scores["Q6"] == 4
    # CPU absent -> "n. v."
    assert base.cpu_max is None


def test_compare_detail_carries_model_delta_per_cell():
    d = tmp = pathlib.Path(_mk_runs(tempfile.mkdtemp())) / "2026_eval_delta"
    _write_compare_bundle(
        tmp,
        cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
            ("m", "none"): {q: 3 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
        },
        perf_by_cell={
            ("m", "baseline"): {"sys_used_mb": 52000.0, "sys_used_delta_mb": 12000.0},
            ("m", "none"): {"sys_used_mb": 50000.0, "sys_used_delta_mb": 10000.0},
        },
    )
    detail = compare.compare_detail(d, "variant")
    assert detail is not None
    base = next(c for c in detail.cells if c.label == "baseline")
    none = next(c for c in detail.cells if c.label == "none")
    assert base.model_delta_mb == 12000.0
    assert none.model_delta_mb == 10000.0


def test_compare_detail_single_axis_value_marks_nothing_to_compare():
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_one"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
        },
    )
    detail = compare.compare_detail(d, "model")  # only 1 model
    assert detail is not None
    assert detail.single is True
    assert len(detail.cells) == 1


def test_relations_summary_states_numbers_and_quality_leader():
    d = _two_variant_bundle(pathlib.Path(tempfile.mkdtemp()))
    detail = compare.compare_detail(d, "variant")
    s = detail.relations_summary
    assert "baseline" in s and "none" in s
    assert "80" in s and "40" in s  # the two quality %s
    assert "Prozentpunkte" in s  # quality-leader clause
    # descriptive only: no hard recommendation verb
    assert "empfehl" not in s.lower()


def test_winners_per_row():
    d = _two_variant_bundle(pathlib.Path(tempfile.mkdtemp()))
    detail = compare.compare_detail(d, "variant")
    assert detail.winners["pct"] == "baseline"  # higher quality
    assert detail.winners["decode"] == "none"  # 14 > 12 tok/s
    assert detail.winners["ttft"] == "none"  # lower TTFT wins
    assert detail.winners["ram"] == "none"  # lower peak RAM wins
    assert detail.winners["Q6"] == "baseline"  # 4 > 2


def test_scatter_points_only_rated_cells():
    d = _two_variant_bundle(pathlib.Path(tempfile.mkdtemp()))
    detail = compare.compare_detail(d, "variant")
    pts = {p["label"]: p for p in detail.scatter_points}
    assert pts["baseline"]["x"] == 12.0
    assert round(pts["baseline"]["y"]) == 80
    assert pts["baseline"]["r"] == 8200.0


def test_divergence_sorted_by_delta():
    """_divergence returns per-prompt entries sorted descending by score delta."""
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_div"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
            ("m", "none"): {q: 2 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
        },
        verdicts_by_cell={
            ("m", "baseline"): [
                Verdict(
                    model="m",
                    variant="baseline",
                    prompt_id="A1",
                    repeat=0,
                    category="A",
                    score=5,
                    red_flag=False,
                    rationale="good",
                )
            ],
            ("m", "none"): [
                Verdict(
                    model="m",
                    variant="none",
                    prompt_id="A1",
                    repeat=0,
                    category="A",
                    score=2,
                    red_flag=False,
                    rationale="weak",
                )
            ],
        },
    )
    detail = compare.compare_detail(d, "variant")
    assert detail is not None
    assert len(detail.divergence) == 1
    dp = detail.divergence[0]
    assert dp.prompt_id == "A1"
    assert dp.delta == pytest.approx(3.0)
    assert dp.scores["baseline"] == pytest.approx(5.0)
    assert dp.scores["none"] == pytest.approx(2.0)
    assert len(dp.answers) == 2


def _verdict(
    model, variant, prompt_id, score, *, repeat=0, red_flag=False, rationale="r", category="A"
):
    return Verdict(
        model=model,
        variant=variant,
        prompt_id=prompt_id,
        repeat=repeat,
        category=category,
        score=score,
        red_flag=red_flag,
        rationale=rationale,
    )


def test_divergence_sorted_by_abs_delta_and_means_repeats():
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_div2"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
            ("m", "none"): {q: 3 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
        },
        verdicts_by_cell={
            ("m", "baseline"): [
                _verdict("m", "baseline", "A1", 5),
                _verdict("m", "baseline", "A2", 4),
                _verdict("m", "baseline", "A2", 2, repeat=1),
            ],
            ("m", "none"): [
                _verdict("m", "none", "A1", 2),
                _verdict("m", "none", "A2", 3),
                _verdict("m", "none", "A2", 3, repeat=1),
            ],
        },
    )
    detail = compare.compare_detail(d, "variant")
    div = detail.divergence
    assert div[0].prompt_id == "A1"  # |5-2| = 3 is the biggest gap, comes first
    assert div[0].scores["baseline"] == 5.0
    assert div[0].scores["none"] == 2.0
    assert div[0].delta == 3.0
    a2 = next(p for p in div if p.prompt_id == "A2")
    assert a2.scores["baseline"] == 3.0  # mean(4,2) over repeats
    assert a2.delta == 0.0
    # answers carry the per-cell response_text + verdict
    ans = {a.label: a for a in div[0].answers}
    assert ans["baseline"].score == 5
    assert ans["none"].score == 2


def test_divergence_empty_when_unjudged():
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_unj"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
            ("m", "none"): {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]},
        },
    )
    detail = compare.compare_detail(d, "variant")
    assert detail.divergence == []


def _two_model_bundle(tmp_dir):
    """Synthetic 2-model × 2-variant bundle (no real bundle covers axis=model)."""
    d = pathlib.Path(tmp_dir) / "2026_eval_2x2"
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    _write_compare_bundle(
        d,
        cells=[("alpha", "baseline"), ("alpha", "none"), ("beta", "baseline"), ("beta", "none")],
        dim_scores_by_cell={
            ("alpha", "baseline"): full,
            ("alpha", "none"): {**full, "Q6": 2},
            ("beta", "baseline"): {q: 5 for q in full},
            ("beta", "none"): full,
        },
        perf_by_cell={
            ("alpha", "baseline"): {"decode_tps": 10.0},
            ("alpha", "none"): {"decode_tps": 11.0},
            ("beta", "baseline"): {"decode_tps": 20.0},
            ("beta", "none"): {"decode_tps": 21.0},
        },
    )
    return d


def test_axis_model_projects_baseline_by_default():
    d = _two_model_bundle(tempfile.mkdtemp())
    detail = compare.compare_detail(d, "model")  # default projection -> baseline
    assert detail.axis == "model"
    assert detail.projection == "baseline"
    assert detail.projection_label == "Variante"
    assert [c.label for c in detail.cells] == ["alpha", "beta"]
    # the projected cells are the baseline variants of each model
    assert all(c.variant == "baseline" for c in detail.cells)
    assert detail.projection_options == ["baseline", "none"]  # switchable


def test_axis_model_projection_override_to_none():
    d = _two_model_bundle(tempfile.mkdtemp())
    detail = compare.compare_detail(d, "model", projection="none")
    assert detail.projection == "none"
    assert all(c.variant == "none" for c in detail.cells)
    alpha = next(c for c in detail.cells if c.label == "alpha")
    assert alpha.rubric_level == "solide"  # alpha/none has Q6=2, pct=72.5% -> solide (safety fails separately)


# ── Review fixes (adversarial 3-perspective review) ───────────────────────────


def test_winners_none_on_tie():
    """A genuine tie must award no trophy (no silent first-cell-wins)."""
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_tie"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={("m", "baseline"): full, ("m", "none"): dict(full)},
        perf_by_cell={
            ("m", "baseline"): {
                "decode_tps": 10.0,
                "ttft_s": 0.1,
                "e2e_s": 1.0,
                "sys_used_mb": 8000.0,
            },
            ("m", "none"): {"decode_tps": 10.0, "ttft_s": 0.1, "e2e_s": 1.0, "sys_used_mb": 8000.0},
        },
    )
    detail = compare.compare_detail(d, "variant")
    assert detail.winners["pct"] is None
    assert detail.winners["decode"] is None
    assert detail.winners["ram"] is None
    assert detail.winners["Q6"] is None


def test_divergence_excludes_unscored_verdicts():
    """V7: unscored verdicts must not count toward the per-prompt mean; all-unscored -> None."""
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_uns"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={("m", "baseline"): full, ("m", "none"): dict(full)},
        verdicts_by_cell={
            ("m", "baseline"): [
                _verdict("m", "baseline", "A1", 5),
                Verdict(
                    model="m",
                    variant="baseline",
                    prompt_id="A1",
                    repeat=1,
                    category="A",
                    score=0,
                    red_flag=False,
                    rationale="judge down",
                    unscored=True,
                ),
            ],
            ("m", "none"): [
                Verdict(
                    model="m",
                    variant="none",
                    prompt_id="A1",
                    repeat=0,
                    category="A",
                    score=0,
                    red_flag=False,
                    rationale="judge down",
                    unscored=True,
                ),
            ],
        },
    )
    detail = compare.compare_detail(d, "variant")
    a1 = next(p for p in detail.divergence if p.prompt_id == "A1")
    assert a1.scores["baseline"] == 5.0  # unscored repeat-1 ignored
    assert a1.scores["none"] is None  # all unscored -> None ("—")


def test_compare_detail_bad_cell_degrades_only_that_group(monkeypatch):
    """Spec §7: a corrupt group must degrade only that column, never collapse the page."""
    d = _two_variant_bundle(pathlib.Path(tempfile.mkdtemp()))
    real = compare._cell_metrics

    def boom(label, model, variant, *a, **k):
        if variant == "none":
            raise ValueError("corrupt group")
        return real(label, model, variant, *a, **k)

    monkeypatch.setattr(compare, "_cell_metrics", boom)
    detail = compare.compare_detail(d, "variant")
    assert detail is not None
    assert [c.label for c in detail.cells] == ["baseline"]  # bad 'none' dropped, page survives


def test_mem_pressure_uses_ok_responses_only():
    """Druck must use the same ok/non-cold population as Peak-RAM (scorecard convention)."""
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_press"
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
    cold = _resp("m", "baseline", is_cold_start=True, mem_pressure_max="critical", prompt_id="A1")
    warm = _resp("m", "baseline", is_cold_start=False, mem_pressure_max="normal", prompt_id="A2")
    (d / "responses.jsonl").write_text(
        json.dumps(cold.as_dict()) + "\n" + json.dumps(warm.as_dict()) + "\n", encoding="utf-8"
    )
    write_reports_jsonl(d / "reports.jsonl", [ModelReport("m", "baseline", dict(full), {})])
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    (d / "judgements.jsonl").write_text("", encoding="utf-8")
    detail = compare.compare_detail(d, "variant")
    assert detail.cells[0].mem_pressure_max == "normal"  # cold-start 'critical' excluded
