"""Pure read/aggregation layer for the within-bundle Modell-/Varianten-Vergleich.

Reads a single bundle (via ``bundles.bundle_detail`` — which already performs the
``master_rows``⨝``reports`` join) and projects it along ONE axis (model or variant),
holding the other dimension constant so exactly one variable changes. Quality comes
from the holistic master scores; cost is speed + RAM (from EvalResponse) + CPU
(GUI-side, from resources.jsonl windows). Nothing here mutates the data model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ramcheck.merge import DEFAULT_TOLERANCE_S, load_samples_jsonl
from ramcheck.models import ResourceSample, pressure_max
from ramcheck.results import EvalResponse, Verdict
from ramcheck.scorecard import model_variant_groups
from ramcheck.stats import median, percentile


@dataclass
class AxisOptions:
    """Which axes are comparable for a bundle (drives the '↔ Vergleichen' link)."""

    models: list[str]
    variants: list[str]
    default_axis: str  # "model" | "variant"
    default_label: str  # "Modell" | "Variante"
    comparable: bool


def _distinct(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def axis_options(responses: list[EvalResponse]) -> AxisOptions:
    """First-seen models/variants + the default comparison axis.

    Default axis is ``model`` when >1 model exists, else ``variant``. ``comparable``
    is True iff at least one axis has >1 distinct value.
    """
    groups = model_variant_groups(responses)
    models = _distinct([m for m, _ in groups])
    variants = _distinct([v for _, v in groups])
    if len(models) > 1:
        axis, label = "model", "Modell"
    else:
        axis, label = "variant", "Variante"
    comparable = len(models) > 1 or len(variants) > 1
    return AxisOptions(models, variants, axis, label, comparable)


def _cpu_for_window(
    samples: list[ResourceSample],
    responses: list[EvalResponse],
    tol: float = DEFAULT_TOLERANCE_S,
) -> tuple[float | None, float | None]:
    """(cpu_max, cpu_avg) over ticks falling in any response's [t_start,t_end±tol]
    window. None-``cpu_pct`` ticks are filtered (today's universal state → 'n. v.').
    Returns (None, None) when no tick carries a cpu_pct value. Windows are verified
    disjoint, so the seen-index guard is just defensive de-duplication.
    """
    vals: list[float] = []
    seen: set[int] = set()
    for r in responses:
        for i, s in enumerate(samples):
            if i in seen:
                continue
            if r.t_start - tol <= s.ts <= r.t_end + tol:
                seen.add(i)
                if s.cpu_pct is not None:
                    vals.append(s.cpu_pct)
    if not vals:
        return None, None
    return max(vals), sum(vals) / len(vals)


@dataclass
class CompareCell:
    """One axis value (a model or a variant) with quality + cost metrics."""

    label: str            # the axis value shown as a column header
    model: str
    variant: str
    pct: float | None
    recommendation: str | None
    safety_passed: bool | None
    safety_reason: str
    dim_scores: dict[str, int] = field(default_factory=dict)
    dim_rationales: dict[str, str] = field(default_factory=dict)
    decode_tps: float | None = None
    ttft_p50: float | None = None
    e2e_med: float | None = None
    peak_ram_mb: float | None = None
    mem_pressure_max: str = "normal"
    cpu_max: float | None = None
    cpu_avg: float | None = None
    n_ok: int = 0


@dataclass
class CompareDetail:
    axis: str                     # resolved: "model" | "variant"
    axis_label: str               # "Modell" | "Variante"
    projection: str               # the held-constant value of the other dimension
    projection_label: str         # "Variante" | "Modell"
    projection_options: list[str] # other-dimension values to switch to (>1 only)
    cells: list[CompareCell]
    relations_summary: str
    winners: dict[str, str | None]
    scatter_points: list[dict]
    divergence: list  # list[DivergencePrompt]; filled in Task 5
    judged: bool
    single: bool
    pack: object              # Pack — for the method explainer + dimension labels
    dimensions: list          # pack.dimensions (id/name/weight) for ② rows
    run_name: str


def _med_or_none(values: list[float]) -> float | None:
    vals = [v for v in values if not math.isnan(v)]
    return median(vals) if vals else None


def _p50_or_none(values: list[float]) -> float | None:
    vals = [v for v in values if not math.isnan(v)]
    return percentile(vals, 50.0) if vals else None


def _cell_metrics(
    label: str,
    model: str,
    variant: str,
    responses: list[EvalResponse],
    master: dict | None,
    dim_scores: dict[str, int],
    dim_rationales: dict[str, str],
    samples: list[ResourceSample],
) -> CompareCell:
    cell_resps = [r for r in responses if (r.model, r.variant) == (model, variant)]
    ok = [r for r in cell_resps if r.ok and not r.is_cold_start]
    sys_used = [r.sys_used_mb for r in ok if r.sys_used_mb is not None]
    levels = [r.mem_pressure_max for r in cell_resps if r.mem_pressure_max]
    cpu_max, cpu_avg = _cpu_for_window(samples, cell_resps)
    return CompareCell(
        label=label, model=model, variant=variant,
        pct=(master["pct"] if master else None),
        recommendation=(master["recommendation"] if master else None),
        safety_passed=(master["safety_passed"] if master else None),
        safety_reason=(master["safety_reason"] if master else ""),
        dim_scores=dict(dim_scores),
        dim_rationales=dict(dim_rationales),
        decode_tps=_med_or_none([r.decode_tps for r in ok]),
        ttft_p50=_p50_or_none([r.ttft_s for r in ok]),
        e2e_med=_med_or_none([r.e2e_s for r in ok]),
        peak_ram_mb=(max(sys_used) if sys_used else None),
        mem_pressure_max=pressure_max(levels),
        cpu_max=cpu_max, cpu_avg=cpu_avg,
        n_ok=len(ok),
    )


def compare_detail(
    run_dir: Path,
    axis: str | None = None,
    *,
    projection: str | None = None,
) -> CompareDetail | None:
    """Project a bundle along ``axis`` (model|variant), holding the other dimension
    constant. Returns None if the bundle has no loadable pack.
    """
    from ramcheck.gui import bundles  # local import avoids a cycle

    base = bundles.bundle_detail(run_dir)
    if base is None:
        return None
    responses: list[EvalResponse] = base["responses"]
    pk = base["pack"]
    reports = base["reports"]
    master_rows = base["master_rows"]
    verdicts = base["verdicts"]
    try:
        samples = load_samples_jsonl(run_dir / "resources.jsonl")
    except Exception:
        samples = []

    groups = model_variant_groups(responses)
    group_set = set(groups)
    models = _distinct([m for m, _ in groups])
    variants = _distinct([v for _, v in groups])
    resolved = axis if axis in ("model", "variant") else ("model" if len(models) > 1 else "variant")

    if resolved == "model":
        axis_label, proj_label = "Modell", "Variante"
        proj = projection if projection in variants else ("baseline" if "baseline" in variants else (variants[0] if variants else ""))
        keys = [(m, proj) for m in models if (m, proj) in group_set]
        proj_options = variants if len(variants) > 1 else []
        labels = [m for m, _ in keys]
    else:
        axis_label, proj_label = "Variante", "Modell"
        proj = projection if projection in models else (models[0] if models else "")
        keys = [(proj, v) for v in variants if (proj, v) in group_set]
        proj_options = models if len(models) > 1 else []
        labels = [v for _, v in keys]

    reports_by = {(r.model, r.variant): r for r in reports}
    master_by = {(row["model"], row["variant"]): row for row in master_rows}

    cells: list[CompareCell] = []
    for (model, variant), label in zip(keys, labels):
        rep = reports_by.get((model, variant))
        cells.append(_cell_metrics(
            label, model, variant, responses,
            master_by.get((model, variant)),
            rep.dim_scores if rep else {},
            rep.dim_rationales if rep else {},
            samples,
        ))

    judged = any(c.pct is not None for c in cells)
    return CompareDetail(
        axis=resolved, axis_label=axis_label, projection=proj, projection_label=proj_label,
        projection_options=proj_options, cells=cells,
        relations_summary="",            # Task 4
        winners={},                      # Task 4
        scatter_points=[],               # Task 4
        divergence=_divergence(cells, responses, verdicts, pk),
        judged=judged, single=len(cells) <= 1,
        pack=pk, dimensions=list(pk.dimensions), run_name=run_dir.name,
    )


@dataclass
class CellAnswer:
    label: str
    model: str
    variant: str
    response_text: str
    content_empty: bool
    reasoning_chars: int
    score: int | None
    rationale: str
    red_flag: bool
    unscored: bool


@dataclass
class DivergencePrompt:
    prompt_id: str
    category: str
    title: str
    scores: dict[str, float | None]  # axis label -> mean Verdict.score (None if absent)
    delta: float                     # |max - min| over present means
    answers: list[CellAnswer]


def _mean_score(verdicts: list[Verdict]) -> float | None:
    vals = [v.score for v in verdicts if not v.unscored]
    return sum(vals) / len(vals) if vals else None


def _first_answer(label, model, variant, responses, verdicts) -> CellAnswer | None:
    resps = [r for r in responses if (r.model, r.variant) == (model, variant)]
    if not resps:
        return None
    r = resps[0]
    v = next((x for x in verdicts if (x.model, x.variant, x.repeat) == (model, variant, r.repeat)), None)
    return CellAnswer(
        label=label, model=model, variant=variant,
        response_text=r.response_text, content_empty=r.content_empty,
        reasoning_chars=r.reasoning_chars,
        score=(v.score if v else None), rationale=(v.rationale if v else ""),
        red_flag=(v.red_flag if v else False), unscored=(v.unscored if v else False),
    )


def _divergence(
    cells: list[CompareCell],
    responses: list[EvalResponse],
    verdicts: list[Verdict],
    pk,
) -> list[DivergencePrompt]:
    if len(cells) < 2 or not verdicts:
        return []
    prompt_meta = {p.id: (cat.id, p.title) for cat, p in pk.all_prompts()}
    prompt_ids: list[str] = []
    for v in verdicts:
        if v.prompt_id not in prompt_ids:
            prompt_ids.append(v.prompt_id)

    out: list[DivergencePrompt] = []
    for pid in prompt_ids:
        scores: dict[str, float | None] = {}
        answers: list[CellAnswer] = []
        for c in cells:
            cell_verdicts = [
                v for v in verdicts
                if (v.model, v.variant, v.prompt_id) == (c.model, c.variant, pid)
            ]
            scores[c.label] = _mean_score(cell_verdicts)
            ans = _first_answer(
                c.label, c.model, c.variant,
                [r for r in responses if r.prompt_id == pid],
                cell_verdicts,
            )
            if ans is not None:
                answers.append(ans)
        present = [s for s in scores.values() if s is not None]
        delta = (max(present) - min(present)) if len(present) >= 2 else 0.0
        cat, title = prompt_meta.get(pid, ("", pid))
        out.append(DivergencePrompt(pid, cat, title, scores, delta, answers))

    out.sort(key=lambda p: p.delta, reverse=True)
    return out


def axis_options_for_dir(run_dir: Path) -> AxisOptions:
    """Robustly load responses.jsonl and report axis options (for overview links)."""
    from ramcheck.qualrun import load_responses_jsonl

    try:
        responses = load_responses_jsonl(run_dir / "responses.jsonl")
    except Exception:
        responses = []
    return axis_options(responses)
