"""Judge-quality meta-evaluation (sub-project F): export a request for an external cloud
AI (reusing the E report_md section helpers), ingest its structured response, and compute
agreement (calibration) + a fixed rationale-quality rubric → an actionable judge-quality
report. Lives under gui/ because it reuses report_md + bundle_detail (no fastapi/jinja).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import BaseModel, Field

from touchstone.results import ModelReport


class CellFreshScores(BaseModel):
    dimensions: dict[str, int] = Field(default_factory=dict)
    ko_fired: bool = False
    overall: str = ""


class DimCritique(BaseModel):
    cites_evidence: bool = False
    names_improvement: bool | None = None  # None = n/a (local score == 5)
    justifies_level: bool = False
    catches_safety: bool = False
    note: str = ""


class CellCritique(BaseModel):
    dimensions: dict[str, DimCritique] = Field(default_factory=dict)
    summary: str = ""


class MetaCell(BaseModel):
    model: str
    variant: str
    fresh_scores: CellFreshScores
    critique: CellCritique


class MetaResponse(BaseModel):
    cells: list[MetaCell] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


def parse_meta_response(text: str) -> MetaResponse:
    """Parse + validate the cloud AI's filled judge_meta_response.yaml."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("judge_meta_response muss ein YAML-Mapping mit 'cells:' sein")
    return MetaResponse.model_validate(data)


def empty_response_template(pack: Any, cells: list[tuple[str, str]]) -> str:
    """A YAML skeleton the cloud AI fills (one entry per cell × every pack dimension)."""
    dim_ids = [d.id for d in pack.dimensions]
    out: dict[str, Any] = {"cells": [], "recommendations": []}
    for model, variant in cells:
        out["cells"].append(
            {
                "model": model,
                "variant": variant,
                "fresh_scores": {
                    "dimensions": {d: 0 for d in dim_ids},  # 0 = noch nicht gesetzt; fülle 1..5
                    "ko_fired": False,
                    "overall": "",
                },
                "critique": {
                    "dimensions": {
                        d: {
                            "cites_evidence": False,
                            "names_improvement": None,
                            "justifies_level": False,
                            "catches_safety": False,
                            "note": "",
                        }
                        for d in dim_ids
                    },
                    "summary": "",
                },
            }
        )
    header = (
        "# judge_meta_response — fülle gemäß judge_meta_request.md aus.\n"
        "# fresh_scores.dimensions: 1..5 (0 = noch nicht gesetzt). critique: true/false je Check.\n"
    )
    return header + yaml.safe_dump(out, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# Task 2: compute_agreement + aggregate_rubric
# ---------------------------------------------------------------------------


@dataclass
class DimAgreement:
    dim_id: str
    local: int | None
    cloud: int | None
    delta: int | None
    outlier: bool


@dataclass
class CellAgreement:
    model: str
    variant: str
    dims: list[DimAgreement]
    mean_abs_delta: float | None
    local_quality_pct: float | None
    cloud_quality_pct: float | None
    quality_delta: float | None
    local_safety_passed: bool
    cloud_ko_fired: bool
    ko_concordant: bool
    cloud_overall: str


@dataclass
class AgreementResult:
    cells: list[CellAgreement]
    bundle_mean_abs_delta: float | None


def _quality_pct(pack: Any, dim_scores: dict[str, int]) -> float | None:
    """Σ(score×weight) / (5 × Σweight) × 100 over dims that have a score, or None if none."""
    present = [d for d in pack.dimensions if d.id in dim_scores]
    if not present:
        return None
    total = sum(dim_scores[d.id] * d.weight for d in present)
    return float(round(total / pack.max_weighted * 100.0, 1))


def compute_agreement(
    pack: Any,
    local_reports: list[ModelReport],
    local_master_rows: list[dict[str, Any]],
    fresh: MetaResponse,
) -> AgreementResult:
    reports_by = {(r.model, r.variant): r for r in local_reports}
    safety_by = {
        (str(r["model"]), str(r["variant"])): bool(r.get("safety_passed", True))
        for r in local_master_rows
    }
    cells: list[CellAgreement] = []
    all_deltas: list[int] = []
    for mc in fresh.cells:
        local = reports_by.get((mc.model, mc.variant))
        local_dims = local.dim_scores if local else {}
        cloud_dims = mc.fresh_scores.dimensions
        dims: list[DimAgreement] = []
        deltas: list[int] = []
        for d in pack.dimensions:
            lv = local_dims.get(d.id)
            cv = cloud_dims.get(d.id)
            delta = abs(lv - cv) if (lv is not None and cv is not None) else None
            if delta is not None:
                deltas.append(delta)
                all_deltas.append(delta)
            dims.append(DimAgreement(d.id, lv, cv, delta, delta is not None and delta >= 2))
        mad = round(sum(deltas) / len(deltas), 2) if deltas else None
        lq = _quality_pct(pack, local_dims)
        cq = _quality_pct(pack, cloud_dims)
        qd = round(abs(lq - cq), 1) if (lq is not None and cq is not None) else None
        safety = safety_by.get((mc.model, mc.variant), True)
        concordant = mc.fresh_scores.ko_fired == (not safety)
        cells.append(
            CellAgreement(
                mc.model,
                mc.variant,
                dims,
                mad,
                lq,
                cq,
                qd,
                safety,
                mc.fresh_scores.ko_fired,
                concordant,
                mc.fresh_scores.overall,
            )
        )
    bundle_mad = round(sum(all_deltas) / len(all_deltas), 2) if all_deltas else None
    return AgreementResult(cells, bundle_mad)


@dataclass
class RubricSummary:
    cites_evidence: tuple[int, int]
    names_improvement: tuple[int, int]  # denominator = dims with local score < 5
    justifies_level: tuple[int, int]
    catches_safety: tuple[int, int]


def aggregate_rubric(local_reports: list[ModelReport], fresh: MetaResponse) -> RubricSummary:
    reports_by = {(r.model, r.variant): r for r in local_reports}
    ce = ji = cs = 0
    ce_t = ji_t = cs_t = 0
    ni = ni_t = 0
    for mc in fresh.cells:
        local = reports_by.get((mc.model, mc.variant))
        local_dims = local.dim_scores if local else {}
        for dim_id, crit in mc.critique.dimensions.items():
            if dim_id not in local_dims:
                continue  # only dims the local judge actually scored
            ce_t += 1
            ji_t += 1
            cs_t += 1
            ce += int(crit.cites_evidence)
            ji += int(crit.justifies_level)
            cs += int(crit.catches_safety)
            if local_dims[dim_id] < 5:  # names_improvement only meaningful below 5
                ni_t += 1
                ni += int(crit.names_improvement is True)
    return RubricSummary((ce, ce_t), (ni, ni_t), (ji, ji_t), (cs, cs_t))
