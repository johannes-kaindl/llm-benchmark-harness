from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import ValidationError

from touchstone.gui.judge_meta import (
    MetaResponse,
    aggregate_rubric,
    compute_agreement,
    empty_response_template,
    parse_meta_response,
    render_judge_quality_md,
)
from touchstone.pack import load_pack
from touchstone.results import ModelReport

PACK = "packs/ndassist.yaml"

VALID_YAML = """
cells:
  - model: m
    variant: baseline
    fresh_scores:
      dimensions: {Q1: 4, Q2: 3}
      ko_fired: false
      overall: "Ja"
    critique:
      dimensions:
        Q1: {cites_evidence: true, names_improvement: false, justifies_level: true, catches_safety: true, note: "ok"}
      summary: "solide"
recommendations:
  - "Bei Score < 5 immer benennen, was besser wäre."
"""


def test_parse_valid_response():
    r = parse_meta_response(VALID_YAML)
    assert isinstance(r, MetaResponse)
    assert r.cells[0].model == "m" and r.cells[0].variant == "baseline"
    assert r.cells[0].fresh_scores.dimensions == {"Q1": 4, "Q2": 3}
    assert r.cells[0].critique.dimensions["Q1"].names_improvement is False
    assert r.recommendations == ["Bei Score < 5 immer benennen, was besser wäre."]


def test_parse_rejects_non_mapping():
    with pytest.raises(ValueError):
        parse_meta_response("- just\n- a\n- list\n")


def test_parse_rejects_bad_score_type():
    with pytest.raises(ValidationError):
        parse_meta_response(
            "cells:\n  - model: m\n    variant: v\n"
            "    fresh_scores: {dimensions: {Q1: not_an_int}, ko_fired: false, overall: x}\n"
            "    critique: {dimensions: {}, summary: ''}\n"
        )


def test_empty_template_round_trips():
    pk = load_pack(PACK)
    cells = [("m", "baseline"), ("m", "none")]
    text = empty_response_template(pk, cells)
    r = parse_meta_response(text)
    assert [(c.model, c.variant) for c in r.cells] == cells
    # every pack dimension is present in both fresh_scores and critique for each cell
    dim_ids = {d.id for d in pk.dimensions}
    for c in r.cells:
        assert set(c.fresh_scores.dimensions) == dim_ids
        assert set(c.critique.dimensions) == dim_ids


class _Dim:
    def __init__(self, id, weight):
        self.id, self.name, self.weight = id, id, weight


class _Ko:
    dimension = "Q2"
    threshold = 2
    red_flag_prompts: ClassVar[list[str]] = []


class _Pack:
    def __init__(self):
        self.id = "ndassist"
        self.dimensions = [_Dim("Q1", 1), _Dim("Q2", 1)]
        self.ko_rule = _Ko()

    @property
    def max_weighted(self):
        return 5 * sum(d.weight for d in self.dimensions)


def _fresh(dims, ko=False, overall="Ja"):
    return parse_meta_response(
        "cells:\n  - model: m\n    variant: baseline\n"
        f"    fresh_scores: {{dimensions: {dims}, ko_fired: {str(ko).lower()}, overall: {overall}}}\n"
        "    critique: {dimensions: {}, summary: ''}\n"
    )


def test_compute_agreement_deltas_and_outliers():
    pk = _Pack()
    local = [ModelReport(model="m", variant="baseline", dim_scores={"Q1": 4, "Q2": 2})]
    rows = [{"model": "m", "variant": "baseline", "safety_passed": False}]
    fresh = _fresh("{Q1: 4, Q2: 4}", ko=True)  # Q2: local 2 vs cloud 4 → Δ2 outlier
    res = compute_agreement(pk, local, rows, fresh)
    cell = res.cells[0]
    d = {x.dim_id: x for x in cell.dims}
    assert d["Q1"].delta == 0 and d["Q1"].outlier is False
    assert d["Q2"].delta == 2 and d["Q2"].outlier is True
    assert cell.mean_abs_delta == 1.0
    # Quality% over max_weighted=10: local (4+2)=6→60%, cloud (4+4)=8→80%, Δ20
    assert cell.local_quality_pct == 60.0 and cell.cloud_quality_pct == 80.0
    assert cell.quality_delta == 20.0
    # safety_passed False → expected ko; cloud ko_fired True → concordant
    assert cell.ko_concordant is True
    assert res.bundle_mean_abs_delta == 1.0


def test_aggregate_rubric_names_improvement_only_below_5():
    pk_local = [
        ModelReport(model="m", variant="baseline", dim_scores={"Q1": 5, "Q2": 3})
    ]  # Q1==5 → names_improvement n/a; Q2<5 → counts
    fresh = parse_meta_response(
        "cells:\n  - model: m\n    variant: baseline\n"
        "    fresh_scores: {dimensions: {}, ko_fired: false, overall: x}\n"
        "    critique:\n      dimensions:\n"
        "        Q1: {cites_evidence: true, names_improvement: true, justifies_level: true, catches_safety: true}\n"
        "        Q2: {cites_evidence: true, names_improvement: false, justifies_level: false, catches_safety: true}\n"
        "      summary: ''\n"
    )
    rub = aggregate_rubric(pk_local, fresh)
    # cites_evidence: both dims pass → 2/2
    assert rub.cites_evidence == (2, 2)
    # names_improvement denominator = only Q2 (local<5); Q2 is False → 0/1 (Q1 excluded as n/a)
    assert rub.names_improvement == (0, 1)
    # justifies_level: Q1 true, Q2 false → 1/2
    assert rub.justifies_level == (1, 2)


def test_render_judge_quality_md_has_all_sections():
    pk = _Pack()
    local = [ModelReport(model="m", variant="baseline", dim_scores={"Q1": 4, "Q2": 5})]
    rows = [{"model": "m", "variant": "baseline", "safety_passed": True}]
    fresh = parse_meta_response(
        "cells:\n  - model: m\n    variant: baseline\n"
        "    fresh_scores: {dimensions: {Q1: 4, Q2: 2}, ko_fired: true, overall: Nein}\n"
        "    critique:\n      dimensions:\n"
        "        Q1: {cites_evidence: false, names_improvement: false, justifies_level: true, catches_safety: true, note: 'oberflächlich'}\n"
        "      summary: 'Q2 unterbewertet'\n"
        "recommendations:\n  - 'Bei Score < 5 benennen, was besser wäre.'\n"
    )
    agg = compute_agreement(pk, local, rows, fresh)
    rub = aggregate_rubric(local, fresh)
    detail = {"run_dir": Path("runs/2026_eval_x"), "manifest": {"judge": {"model": "qwen3-27b"}}}
    md = render_judge_quality_md(detail, agg, rub, fresh)
    assert md.startswith("---\n") and 'type: "judge_quality"' in md
    assert "## Headline" in md
    assert "## 1. Agreement" in md and "## 2. Begründungs-Qualität" in md
    assert "## 3. Empfohlene Judge-Prompt-Verbesserungen" in md
    assert "Bei Score < 5 benennen, was besser wäre." in md  # recommendation rendered
    assert "🚩" in md  # Q2 Δ2 outlier flagged in the agreement table
    assert "qwen3-27b" in md  # judged-by judge model surfaced


def test_render_request_md_partA_blank_partB_visible(tmp_path):
    import json

    from touchstone.gui import bundles
    from touchstone.pack import load_pack

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    d = tmp_path / "2026_eval_nd"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "host": {"chip": "M5", "ram_gb": "64 GB"},
                "date": "2026-06-24",
                "judge": {"model": "qwen3-27b"},
            }
        ),
        encoding="utf-8",
    )
    base = {
        "pack_id": pk.id,
        "pack_version": 1,
        "machine": "t",
        "model": "m",
        "quant": "q4",
        "engine": "lm-studio",
        "engine_version": "0",
        "variant": "baseline",
        "category": "A",
        "prompt_id": first.id,
        "repeat": 0,
        "response_text": "Eine Antwort.",
        "content_empty": False,
        "ttft_s": 0.2,
        "decode_tps": 30.0,
        "prefill_tps": 90.0,
        "e2e_s": 1.5,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "is_cold_start": False,
        "power_source": "ac",
        "peak_rss_mb": 0.0,
        "sys_used_mb": 20000.0,
        "mem_pressure_max": "normal",
        "throttled": False,
        "ok": True,
        "error": "",
        "seed": 42,
        "t_start": 0.0,
        "t_end": 1.5,
        "reasoning_chars": 0,
    }
    (d / "responses.jsonl").write_text(json.dumps(base) + "\n", encoding="utf-8")
    header = "model,variant,metric_type,metric,weight,score"
    rep_rows = [header] + [f"m,baseline,dimension,{dim.id},{dim.weight},4" for dim in pk.dimensions]
    (d / "scores.csv").write_text("\n".join(rep_rows) + "\n", encoding="utf-8")
    from touchstone.judge import write_reports_jsonl
    from touchstone.results import ModelReport

    write_reports_jsonl(
        d / "reports.jsonl",
        [
            ModelReport(
                model="m",
                variant="baseline",
                dim_scores={dim.id: 4 for dim in pk.dimensions},
                dim_rationales={pk.dimensions[0].id: "gut"},
            )
        ],
    )

    from touchstone.gui.judge_meta import render_request_md

    detail = bundles.bundle_detail(d)
    md = render_request_md(detail)
    # ordering instruction present
    assert "Teil A" in md and "bevor" in md
    # Part A = blank Bewertungs-Auftrag (a fillable scorecard row with empty score cell)
    assert "## 📋 Bewertungs-Auftrag" in md
    assert "| Score (1–5) | Begründung" in md
    # Part B = the LOCAL judge's scorecard (visible scores+rationale)
    assert "## Master-Scorecard" in md
    assert "gut" in md  # the local rationale appears in Part B


def test_render_judge_quality_md_frontmatter_enriched():
    pk = _Pack()
    local = [ModelReport(model="m", variant="baseline", dim_scores={"Q1": 4, "Q2": 5})]
    rows = [{"model": "m", "variant": "baseline", "safety_passed": True}]
    fresh = parse_meta_response(
        "cells:\n  - model: m\n    variant: baseline\n"
        "    fresh_scores: {dimensions: {Q1: 4, Q2: 2}, ko_fired: false, overall: Ja}\n"
        "    critique:\n      dimensions:\n"
        "        Q1: {cites_evidence: false, names_improvement: false, justifies_level: true, catches_safety: true}\n"
        "      summary: ''\n"
    )
    agg = compute_agreement(pk, local, rows, fresh)
    rub = aggregate_rubric(local, fresh)
    detail = {
        "run_dir": Path("runs/2026_eval_x"),
        "manifest": {"judge": {"model": "j"}},
        "pack": pk,
    }
    md = render_judge_quality_md(detail, agg, rub, fresh)
    # enriched frontmatter: pack id + the 3 previously body-only rubric rates
    assert "pack: ndassist" in md
    # Q1 is the only scored critique dim (local<5): cites false→0/1, justifies true→1/1, safety true→1/1
    assert "cites_evidence_rate: 0.0" in md
    assert "justifies_level_rate: 1.0" in md
    assert "catches_safety_rate: 1.0" in md


def test_render_judge_quality_md_tolerates_missing_pack():
    # An older caller may pass a detail without "pack" — must not raise; frontmatter pack: —
    pk = _Pack()
    local = [ModelReport(model="m", variant="baseline", dim_scores={"Q1": 4, "Q2": 5})]
    rows = [{"model": "m", "variant": "baseline", "safety_passed": True}]
    fresh = parse_meta_response(
        "cells:\n  - model: m\n    variant: baseline\n"
        "    fresh_scores: {dimensions: {Q1: 4, Q2: 2}, ko_fired: false, overall: Ja}\n"
        "    critique: {dimensions: {}, summary: ''}\n"
    )
    agg = compute_agreement(pk, local, rows, fresh)
    rub = aggregate_rubric(local, fresh)
    detail = {"run_dir": Path("runs/x"), "manifest": {}}  # no "pack" key
    md = render_judge_quality_md(detail, agg, rub, fresh)
    assert "pack: —" in md
