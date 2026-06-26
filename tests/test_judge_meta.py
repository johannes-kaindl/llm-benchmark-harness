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
