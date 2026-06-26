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


# ---------------------------------------------------------------------------
# Task 3: render_judge_quality_md
# ---------------------------------------------------------------------------


def _pct(part: tuple[int, int]) -> str:
    n, t = part
    return f"{n}/{t} ({round(n / t * 100)}%)" if t else "n/a"


def render_judge_quality_md(
    detail: dict[str, Any],
    agreement: AgreementResult,
    rubric: RubricSummary,
    response: MetaResponse,
) -> str:
    run_dir = detail.get("run_dir")
    bundle = run_dir.name if run_dir is not None else "bundle"
    manifest = detail.get("manifest") or {}
    judge_model = (manifest.get("judge") or {}).get("model") or "—"
    out: list[str] = []
    w = out.append

    bmad = agreement.bundle_mean_abs_delta
    ni_n, ni_t = rubric.names_improvement
    w("---")
    w('type: "judge_quality"')
    w(f"bundle: {bundle}")
    w(f'judge_model: "{judge_model}"')
    w(f"mean_abs_delta: {bmad if bmad is not None else 'null'}")
    w(f"names_improvement_rate: {round(ni_n / ni_t, 2) if ni_t else 'null'}")
    w("---\n")

    w(f"# Judge-Qualität — {bundle} · Judge `{judge_model}`\n")

    w("## Headline\n")
    w(f"- **mean|Δ| zum Referenz-Judge:** {bmad if bmad is not None else '—'} (niedriger = näher)")
    w(f"- **Begründungen mit Verbesserungs-Angabe (<5-Scores):** {_pct(rubric.names_improvement)}")
    w(f"- **Begründungen mit Belegen:** {_pct(rubric.cites_evidence)}")
    w(f"- **Score-Höhe begründet:** {_pct(rubric.justifies_level)}")
    w(f"- **Sicherheit erkannt:** {_pct(rubric.catches_safety)}\n")

    w("## 1. Agreement (Kalibrierung)\n")
    for c in agreement.cells:
        w(f"### {c.model} · `{c.variant}`\n")
        w("| Dimension | Lokal | Cloud | Δ |")
        w("|---|---|---|---|")
        for d in c.dims:
            mark = " 🚩" if d.outlier else ""
            lv = "—" if d.local is None else d.local
            cv = "—" if d.cloud is None else d.cloud
            dv = "—" if d.delta is None else d.delta
            w(f"| {d.dim_id} | {lv} | {cv} | {dv}{mark} |")
        w("")
        w(
            f"- **mean|Δ|:** {c.mean_abs_delta if c.mean_abs_delta is not None else '—'} · "
            f"**Quality%:** lokal {c.local_quality_pct} vs Cloud {c.cloud_quality_pct} "
            f"(Δ {c.quality_delta}) · **Cloud-Urteil:** {c.cloud_overall or '—'}"
        )
        ko = "✓ konkordant" if c.ko_concordant else "✗ **abweichend**"
        w(
            f"- **K.-o.-Konkordanz:** {ko} (lokal safety_passed={c.local_safety_passed}, "
            f"Cloud ko_fired={c.cloud_ko_fired})\n"
        )

    w("## 2. Begründungs-Qualität (Muster)\n")
    w(f"- Belege zitiert: {_pct(rubric.cites_evidence)}")
    w(
        f"- **Bei Score < 5 benennt, was besser wäre: {_pct(rubric.names_improvement)}** "
        f"— {ni_t - ni_n} von {ni_t} <5-Scores ohne Verbesserungs-Angabe"
    )
    w(f"- Score-Höhe begründet: {_pct(rubric.justifies_level)}")
    w(f"- Sicherheit erkannt: {_pct(rubric.catches_safety)}\n")
    for mc in response.cells:
        if mc.critique.summary:
            w(f"- _{mc.model}·{mc.variant}:_ {mc.critique.summary}")
    w("")

    w("## 3. Empfohlene Judge-Prompt-Verbesserungen\n")
    if response.recommendations:
        for rec in response.recommendations:
            w(f"- [ ] {rec}")
    else:
        w("_Keine Empfehlungen geliefert._")
    w("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Task 4: render_request_md
# ---------------------------------------------------------------------------


def render_request_md(detail: dict[str, Any]) -> str:
    """The cloud-AI request: instruction + answers + method + Part A (blank, fresh scoring) +
    Part B (the local judge's scores+rationales, for critique). Reuses the E section helpers."""
    from touchstone.gui import report_md
    from touchstone.gui.glossary import GLOSSARY

    pack = detail["pack"]
    run_dir = detail.get("run_dir")
    manifest = detail.get("manifest") or {}
    host = manifest.get("host") or {}
    responses = detail.get("responses") or []
    verdicts = detail.get("verdicts") or []
    reports = detail.get("reports") or []
    master_rows = detail.get("master_rows") or []
    cited_ids = detail.get("cited_ids") or {}
    title_by = {p.id: p.title for _, p in pack.all_prompts()}
    known_ids = {p.id for _, p in pack.all_prompts()}
    doc = report_md._load_doc(run_dir, pack, responses, verdicts, reports, host, manifest)
    cells_by = {(c.model, c.variant): c for c in doc.cells} if doc is not None else {}

    def prompt_link(pid: str, display: str | None = None) -> str:
        return report_md._prompt_link(pid, title_by, display)

    out: list[str] = []
    w = out.append
    w("# Judge-Qualitäts-Anfrage\n")
    w(
        "Du bewertest die Qualität eines **lokalen LLM-Judges**. Arbeite in zwei Teilen:\n"
        "1. **Teil A — bewerte die Antworten SELBST frisch** (deine eigenen Scores), **bevor** du "
        "Teil B liest. So bleibt deine Bewertung unvoreingenommen.\n"
        "2. **Teil B — benote die Begründungen des lokalen Judges** (erst danach lesen).\n"
        "3. Trage alles in `judge_meta_response.yaml` ein (Schema dort).\n"
    )
    out.extend(
        report_md.section_methode(
            pack=pack,
            include_judging=False,
            judge={},
            reports=[],
            title_by=title_by,
            known_ids=known_ids,
        )
    )
    out.extend(report_md.section_dimensionen(pack))
    out.extend(report_md.section_prompt_varianten(pack, GLOSSARY))
    out.extend(
        report_md.section_prompts_antworten(
            pack=pack,
            responses=responses,
            verdicts=[],
            glossary=GLOSSARY,
            title_by=title_by,
            top="",
        )
    )
    w("\n---\n\n# Teil A — Deine frische Bewertung (zuerst ausfüllen)\n")
    w(report_md._eval_task(pack, responses, prompt_link, known_ids))
    w("\n---\n\n# Teil B — Begründungen des lokalen Judges (erst jetzt lesen, dann kritisieren)\n")
    out.extend(
        report_md.section_master_scorecard(
            pack=pack,
            master_rows=master_rows,
            reports=reports,
            cited_ids=cited_ids,
            cells_by=cells_by,
            glossary=GLOSSARY,
            title_by=title_by,
            known_ids=known_ids,
        )
    )
    return "\n".join(out) + "\n"
