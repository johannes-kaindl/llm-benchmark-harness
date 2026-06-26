"""Judge-quality meta-evaluation (sub-project F): export a request for an external cloud
AI (reusing the E report_md section helpers), ingest its structured response, and compute
agreement (calibration) + a fixed rationale-quality rubric → an actionable judge-quality
report. Lives under gui/ because it reuses report_md + bundle_detail (no fastapi/jinja).
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field


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
