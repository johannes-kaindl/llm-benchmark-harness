from __future__ import annotations

import json
import pathlib
import tempfile

from ramcheck.gui import compare
from ramcheck.judge import write_reports_jsonl
from ramcheck.models import ResourceSample
from ramcheck.pack import load_pack
from ramcheck.results import EvalResponse, ModelReport, Verdict

PACK = "packs/ndassist.yaml"


def _resp(model: str, variant: str, **over) -> EvalResponse:
    base = dict(
        pack_id="ndassist", pack_version=1, machine="t", model=model, quant="q",
        engine="e", engine_version="x", variant=variant, category="A", prompt_id="A1",
        repeat=0, response_text="ok", content_empty=False, ttft_s=0.1, decode_tps=10.0,
        prefill_tps=1.0, e2e_s=1.0, prompt_tokens=1, completion_tokens=1,
        is_cold_start=False, power_source="ac", peak_rss_mb=0.0, sys_used_mb=8000.0,
        mem_pressure_max="normal", throttled=False, ok=True, error="", seed=42,
        t_start=0.0, t_end=1.0, reasoning_chars=0,
    )
    base.update(over)
    return EvalResponse(**base)


def _sample(ts: float, cpu: float | None) -> ResourceSample:
    return ResourceSample(
        ts=ts, sys_used_mb=1.0, sys_available_mb=1.0, swap_used_mb=0.0,
        server_rss_mb=None, mem_pressure_level="normal", throttled=False, cpu_pct=cpu,
    )


def _write_compare_bundle(
    d,
    *,
    cells,                 # list[(model, variant)]
    dim_scores_by_cell,    # {(model,variant): {dim_id: int}}
    perf_by_cell=None,     # {(model,variant): {"decode_tps","ttft_s","e2e_s","sys_used_mb"}}
    verdicts_by_cell=None, # {(model,variant): list[Verdict]}
    rationales_by_cell=None,
    samples=None,          # list[ResourceSample] | None  (resources.jsonl)
):
    """Self-contained judged bundle using the real in-repo pack."""
    d.mkdir(parents=True, exist_ok=True)
    pk = load_pack(PACK)
    (d / "bundle.json").write_text(
        json.dumps({
            "pack_id": pk.id, "pack_path": PACK,
            "models": [{"id": m, "quant": "q"} for m, _ in cells],
            "date": "2026-06-20", "host": {"machine": "t"},
        }),
        encoding="utf-8",
    )
    # responses.jsonl: one OK non-cold response per cell (A1), perf overridable
    perf_by_cell = perf_by_cell or {}
    resp_lines = []
    for m, v in cells:
        p = perf_by_cell.get((m, v), {})
        resp_lines.append(json.dumps(_resp(
            m, v,
            decode_tps=p.get("decode_tps", 10.0),
            ttft_s=p.get("ttft_s", 0.1),
            e2e_s=p.get("e2e_s", 1.0),
            sys_used_mb=p.get("sys_used_mb", 8000.0),
        ).as_dict()))
    (d / "responses.jsonl").write_text("\n".join(resp_lines) + "\n", encoding="utf-8")
    # reports.jsonl: holistic dim_scores (+ optional rationales)
    rationales_by_cell = rationales_by_cell or {}
    reports = [
        ModelReport(m, v, dict(dim_scores_by_cell[(m, v)]), dict(rationales_by_cell.get((m, v), {})))
        for m, v in cells
    ]
    write_reports_jsonl(d / "reports.jsonl", reports)
    # scores.csv stub so classify() reads 'judged' (bundle_detail prefers reports.jsonl)
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    # judgements.jsonl (optional, per-prompt verdicts for divergence)
    verdicts_by_cell = verdicts_by_cell or {}
    vlines = [json.dumps(v.as_dict()) for vs in verdicts_by_cell.values() for v in vs]
    (d / "judgements.jsonl").write_text(("\n".join(vlines) + "\n") if vlines else "", encoding="utf-8")
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
            ("m", "baseline"): {"decode_tps": 12.0, "ttft_s": 0.20, "e2e_s": 1.5, "sys_used_mb": 8200.0},
            ("m", "none"): {"decode_tps": 14.0, "ttft_s": 0.15, "e2e_s": 1.2, "sys_used_mb": 8000.0},
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
    assert base.recommendation == "Ja"
    assert none.recommendation == "Nein"        # Q6=2 -> K.-o.
    assert none.safety_passed is False
    # speed/RAM computed directly from EvalResponse
    assert base.decode_tps == 12.0
    assert base.peak_ram_mb == 8200.0
    assert none.peak_ram_mb == 8000.0
    # dim_scores come from the parallel report, not master_rows
    assert base.dim_scores["Q6"] == 4
    # CPU absent -> "n. v."
    assert base.cpu_max is None


def test_compare_detail_single_axis_value_marks_nothing_to_compare():
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_one"
    _write_compare_bundle(
        d, cells=[("m", "baseline")],
        dim_scores_by_cell={("m", "baseline"): {q: 4 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]}},
    )
    detail = compare.compare_detail(d, "model")  # only 1 model
    assert detail is not None
    assert detail.single is True
    assert len(detail.cells) == 1
