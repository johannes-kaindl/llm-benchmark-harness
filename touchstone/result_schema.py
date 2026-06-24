"""Canonical per-(model×variant) result.json schema + pure builder.

``ResultDoc`` is the aggregation-ready document written after every eval or
judge run.  It is the single source of truth for downstream consumers
(Markdown report renderer, GUI compare view, cross-machine aggregator).

This module is PURE — no GUI / IO imports.  It may import from
``touchstone.scorecard``, ``touchstone.client``, and ``touchstone.results``
for types and the reused math helpers.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _f(x: object) -> float | None:
    """Float-or-None; NaN → None.  Accepts ``object`` so callers avoid casts."""
    if x is None:
        return None
    if isinstance(x, float):
        return None if math.isnan(x) else x
    if isinstance(x, (int, str)):
        try:
            return float(x)
        except (ValueError, TypeError):
            return None
    return None


def _ram(s: str | None) -> float:
    """Parse ``"64 GB"`` / ``"64.0"`` → float GB, else 0.0."""
    if not s:
        return 0.0
    # strip trailing units and whitespace
    cleaned = s.strip().upper().replace("GB", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# pydantic models
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    chip: str
    ram_gb: float
    os: str
    engine: str | None = None
    engine_version: str | None = None
    runtime: str | None = None
    seed: int
    temperature: float
    pack_id: str
    pack_version: int
    date: str


class JudgeInfo(BaseModel):
    model: str | None = None
    version: str | None = None
    temperature: float | None = None
    seed: int | None = None


class CellPerf(BaseModel):
    ttft_p50: float | None = None
    ttft_p95: float | None = None
    decode_med: float | None = None
    e2e_med: float | None = None
    total_throughput: float | None = None
    model_delta_gb: float | None = None
    peak_ram_gb: float | None = None


class CellQuality(BaseModel):
    dim_scores: dict[str, int]
    pct: float
    rubric_level: str
    safety_passed: bool
    safety_reason: str
    red_flags: list[str]


class ResultCell(BaseModel):
    model: str
    variant: str
    quant: str | None = None
    answer_tokens_med: int | None = None
    perf: CellPerf
    quality: CellQuality | None = None


class ResultDoc(BaseModel):
    schema_version: int = Field(default=1)
    provenance: Provenance
    judge: JudgeInfo | None = None
    cells: list[ResultCell] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------


def build_result_doc(
    pack: Any,
    responses: list[Any],
    verdicts: list[Any],
    reports: list[Any],
    *,
    host: dict[str, str],
    build_meta: Any | None,
    judge: dict[str, Any] | None,
    sampling: dict[str, Any],
) -> ResultDoc:
    from statistics import median

    from touchstone.scorecard import (
        _perf_summary,
        model_variant_groups,
        passes_ko,
        red_flagged_prompts,
        rubric_level,
        weighted_total,
    )

    reports_by = {(r.model, r.variant): r for r in reports}
    bm_quant: dict[str, str] = build_meta.quant_by_model if build_meta else {}
    cells: list[ResultCell] = []

    for model, variant in model_variant_groups(responses):
        g = [r for r in responses if r.model == model and r.variant == variant]
        p = _perf_summary(g)
        toks = [r.completion_tokens for r in g if not getattr(r, "is_cold_start", False)]
        perf = CellPerf(
            ttft_p50=_f(p["ttft_p50"]),
            ttft_p95=_f(p["ttft_p95"]),
            decode_med=_f(p["decode_med"]),
            e2e_med=_f(p["e2e_med"]),
            total_throughput=_f(p.get("total_throughput")),
            model_delta_gb=_f(p.get("model_delta_gb")),
            peak_ram_gb=_f(p.get("peak_ram_gb")),
        )
        quality: CellQuality | None = None
        rep = reports_by.get((model, variant))
        if rep and rep.dim_scores:
            _, _, pct = weighted_total(rep.dim_scores, pack)
            gv = [v for v in verdicts if (v.model, v.variant) == (model, variant)]
            rf = sorted(red_flagged_prompts(gv))
            passed, reason = passes_ko(rep.dim_scores, set(rf), pack)
            quality = CellQuality(
                dim_scores=rep.dim_scores,
                pct=pct,
                rubric_level=rubric_level(pct),
                safety_passed=passed,
                safety_reason=reason,
                red_flags=rf,
            )
        cells.append(
            ResultCell(
                model=model,
                variant=variant,
                quant=bm_quant.get(model) or (g[0].quant if g else None),
                answer_tokens_med=int(median(toks)) if toks else None,
                perf=perf,
                quality=quality,
            )
        )

    prov = Provenance(
        chip=host.get("chip", ""),
        ram_gb=_ram(host.get("ram_gb")),
        os=host.get("macos", ""),
        engine=host.get("engine"),
        engine_version=(build_meta.engine_version if build_meta else None),
        runtime=(build_meta.runtime if build_meta else None),
        seed=sampling["seed"],
        temperature=sampling["temperature"],
        pack_id=pack.id,
        pack_version=pack.version,
        date=host.get("date", ""),
    )
    jinfo = (
        JudgeInfo(
            model=judge.get("model"),
            version=judge.get("version"),
            temperature=judge.get("temperature"),
            seed=judge.get("seed"),
        )
        if judge
        else None
    )
    return ResultDoc(
        provenance=prov,
        judge=jinfo,
        cells=sorted(cells, key=lambda c: (c.model, c.variant)),
    )
