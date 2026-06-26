# Judge-Quality Meta-Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-bundle judge-quality meta-evaluation via export→external-cloud-AI→ingest: agreement vs. a fresh reference scoring + a fixed rationale-quality rubric → actionable judge-prompt improvements + a comparable headline.

**Architecture:** One cohesive module `touchstone/gui/judge_meta.py` (pure analysis + rendering; lives under `gui/` because it reuses `report_md` section helpers and `bundles.bundle_detail` — same package, no `fastapi`/`jinja` needed). Two CLI subcommands under a typer sub-app `judge-meta export|ingest` that **lazy-import** the gui module inside their bodies (precedent: the `gui` command), so the core never imports gui at module load.

**Tech Stack:** Python 3.12, uv, pydantic, PyYAML, typer, pytest, mypy (strict), ruff.

## Global Constraints

- Python 3.12, `uv` only. Ruff line-length 100. mypy strict over `touchstone/`.
- German in user-facing copy / report output; code + identifiers English.
- **Core never imports gui at module load.** `judge_meta.py` lives in `touchstone/gui/`; the CLI subcommands lazy-import it inside their function bodies.
- **Reuse, don't re-implement:** the request doc composes the E-extracted `report_md` section helpers (`_eval_task` for Part A blank scoring, `section_master_scorecard` for Part B local rationales, plus `section_methode`/`section_dimensionen`/`section_prompt_varianten`/`section_prompts_antworten`). `report_md.py` is NOT modified.
- **Bias order:** the request doc puts Part A (blank, no local scores) before Part B (local scores+rationales) + a hard instruction to complete A before reading B.
- **`names_improvement` is n/a when the local dimension score == 5** (excluded from the rubric denominator, never counted as a fail).
- Quality% for BOTH sides = `Σ(score×weight) / pack.max_weighted × 100` (same weighting → comparable). `pack.max_weighted == 5 * Σ weights`.
- Agreement outlier = `|Δ| ≥ 2`.
- Verification gate before merge: `uv run pytest -q` green · `uv run mypy touchstone/` clean · `uv run ruff check . && uv run ruff format --check .` clean · headless smoke.

---

### Task 1: Response models + `parse_meta_response` + `empty_response_template`

**Files:**
- Create: `touchstone/gui/judge_meta.py`
- Test: `tests/test_judge_meta.py`

**Interfaces:**
- Consumes: `touchstone.pack.Pack` (has `.dimensions: list[Dimension]` with `.id`).
- Produces:
  - pydantic models `CellFreshScores`, `DimCritique`, `CellCritique`, `MetaCell`, `MetaResponse`.
  - `def parse_meta_response(text: str) -> MetaResponse` — YAML text → validated model (raises on bad shape).
  - `def empty_response_template(pack: Any, cells: list[tuple[str, str]]) -> str` — a YAML skeleton (one entry per (model, variant) cell × every pack dimension) that round-trips through `parse_meta_response`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_judge_meta.py
import pytest

from touchstone.gui.judge_meta import (
    MetaResponse,
    empty_response_template,
    parse_meta_response,
)
from touchstone.pack import load_pack

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
    with pytest.raises(Exception):
        parse_meta_response("- just\n- a\n- list\n")


def test_parse_rejects_bad_score_type():
    with pytest.raises(Exception):
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_judge_meta.py -q`
Expected: FAIL (`ModuleNotFoundError: touchstone.gui.judge_meta`).

- [ ] **Step 3: Implement**

```python
# touchstone/gui/judge_meta.py
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_judge_meta.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Type-check + lint + commit**

Run: `uv run mypy touchstone/gui/judge_meta.py && uv run ruff check touchstone/gui/judge_meta.py tests/test_judge_meta.py && uv run ruff format --check touchstone/gui/judge_meta.py tests/test_judge_meta.py`
Expected: clean.

```bash
git add touchstone/gui/judge_meta.py tests/test_judge_meta.py
git commit -m "feat(judge_meta): response models + parse + empty template (pure)"
```

---

### Task 2: `compute_agreement` + `aggregate_rubric`

**Files:**
- Modify: `touchstone/gui/judge_meta.py`
- Test: `tests/test_judge_meta.py`

**Interfaces:**
- Consumes: `MetaResponse` (Task 1); `touchstone.results.ModelReport` (`.model`, `.variant`, `.dim_scores: dict[str,int]`); local master rows = `list[dict]` with `"model"`, `"variant"`, `"safety_passed"`; `Pack` (`.dimensions`, `.max_weighted`, `.ko_rule.dimension`).
- Produces:
  - dataclasses `DimAgreement`, `CellAgreement`, `AgreementResult`.
  - `def compute_agreement(pack, local_reports: list[ModelReport], local_master_rows: list[dict[str, Any]], fresh: MetaResponse) -> AgreementResult`.
  - dataclass `RubricSummary` + `def aggregate_rubric(local_reports: list[ModelReport], fresh: MetaResponse) -> RubricSummary`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_judge_meta.py`)

```python
from touchstone.gui.judge_meta import (
    aggregate_rubric,
    compute_agreement,
)
from touchstone.results import ModelReport


class _Dim:
    def __init__(self, id, weight):
        self.id, self.name, self.weight = id, id, weight


class _Ko:
    dimension = "Q2"
    threshold = 2
    red_flag_prompts: list[str] = []


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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_judge_meta.py -k "agreement or rubric" -q`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** (append to `touchstone/gui/judge_meta.py`)

```python
from dataclasses import dataclass

from touchstone.results import ModelReport


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
    return round(total / pack.max_weighted * 100.0, 1)


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
                mc.model, mc.variant, dims, mad, lq, cq, qd, safety,
                mc.fresh_scores.ko_fired, concordant, mc.fresh_scores.overall,
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_judge_meta.py -q`
Expected: PASS (all).

- [ ] **Step 5: Type-check + lint + commit**

Run: `uv run mypy touchstone/gui/judge_meta.py && uv run ruff check touchstone/gui/judge_meta.py tests/test_judge_meta.py && uv run ruff format --check touchstone/gui/judge_meta.py tests/test_judge_meta.py`

```bash
git add touchstone/gui/judge_meta.py tests/test_judge_meta.py
git commit -m "feat(judge_meta): compute_agreement + aggregate_rubric (pure math)"
```

---

### Task 3: `render_judge_quality_md` (the report)

**Files:**
- Modify: `touchstone/gui/judge_meta.py`
- Test: `tests/test_judge_meta.py`

**Interfaces:**
- Consumes: `AgreementResult`, `RubricSummary`, `MetaResponse` (Tasks 1-2); a `detail` dict (`run_dir`, `manifest`).
- Produces: `def render_judge_quality_md(detail: dict[str, Any], agreement: AgreementResult, rubric: RubricSummary, response: MetaResponse) -> str` — self-contained Obsidian Markdown.

- [ ] **Step 1: Write the failing test** (append to `tests/test_judge_meta.py`)

```python
from pathlib import Path

from touchstone.gui.judge_meta import render_judge_quality_md


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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_judge_meta.py -k judge_quality_md -q`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** (append to `touchstone/gui/judge_meta.py`)

```python
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
    for c in response.cells:
        if c.critique.summary:
            w(f"- _{c.model}·{c.variant}:_ {c.critique.summary}")
    w("")

    w("## 3. Empfohlene Judge-Prompt-Verbesserungen\n")
    if response.recommendations:
        for rec in response.recommendations:
            w(f"- [ ] {rec}")
    else:
        w("_Keine Empfehlungen geliefert._")
    w("")
    return "\n".join(out) + "\n"
```

- [ ] **Step 4: Run to verify pass** → `uv run pytest tests/test_judge_meta.py -q` → PASS.
- [ ] **Step 5: Type-check + lint + commit**

```bash
uv run mypy touchstone/gui/judge_meta.py && uv run ruff check touchstone/gui/judge_meta.py tests/test_judge_meta.py && uv run ruff format --check touchstone/gui/judge_meta.py tests/test_judge_meta.py
git add touchstone/gui/judge_meta.py tests/test_judge_meta.py
git commit -m "feat(judge_meta): render_judge_quality_md (headline + agreement + rubric + recs)"
```

---

### Task 4: `render_request_md` (reuses E report_md helpers)

**Files:**
- Modify: `touchstone/gui/judge_meta.py`
- Test: `tests/test_judge_meta.py`

**Interfaces:**
- Consumes: `report_md._eval_task`, `report_md.section_master_scorecard`, `report_md.section_methode`, `report_md.section_dimensionen`, `report_md.section_prompt_varianten`, `report_md.section_prompts_antworten`, `report_md._load_doc`, `report_md._prompt_link`; `touchstone.gui.glossary.GLOSSARY`. A `detail` dict from `bundles.bundle_detail`.
- Produces: `def render_request_md(detail: dict[str, Any]) -> str`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_judge_meta.py`)

Reuse the bundle-writing helper pattern from `tests/test_gui_report_md.py` (it writes a judged bundle over the real ndassist pack). Build a `detail` via `bundles.bundle_detail` and assert structure:

```python
def test_render_request_md_partA_blank_partB_visible(tmp_path):
    import json

    from touchstone.gui import bundles
    from touchstone.pack import load_pack

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    d = tmp_path / "2026_eval_nd"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps({"pack_id": pk.id, "pack_path": PACK, "host": {"chip": "M5", "ram_gb": "64 GB"},
                    "date": "2026-06-24", "judge": {"model": "qwen3-27b"}}),
        encoding="utf-8",
    )
    base = {"pack_id": pk.id, "pack_version": 1, "machine": "t", "model": "m", "quant": "q4",
            "engine": "lm-studio", "engine_version": "0", "variant": "baseline", "category": "A",
            "prompt_id": first.id, "repeat": 0, "response_text": "Eine Antwort.", "content_empty": False,
            "ttft_s": 0.2, "decode_tps": 30.0, "prefill_tps": 90.0, "e2e_s": 1.5, "prompt_tokens": 100,
            "completion_tokens": 50, "is_cold_start": False, "power_source": "ac", "peak_rss_mb": 0.0,
            "sys_used_mb": 20000.0, "mem_pressure_max": "normal", "throttled": False, "ok": True,
            "error": "", "seed": 42, "t_start": 0.0, "t_end": 1.5, "reasoning_chars": 0}
    (d / "responses.jsonl").write_text(json.dumps(base) + "\n", encoding="utf-8")
    header = "model,variant,metric_type,metric,weight,score"
    rep_rows = [header] + [f"m,baseline,dimension,{dim.id},{dim.weight},4" for dim in pk.dimensions]
    (d / "scores.csv").write_text("\n".join(rep_rows) + "\n", encoding="utf-8")
    from touchstone.judge import write_reports_jsonl
    from touchstone.results import ModelReport
    write_reports_jsonl(
        d / "reports.jsonl",
        [ModelReport(model="m", variant="baseline",
                     dim_scores={dim.id: 4 for dim in pk.dimensions},
                     dim_rationales={pk.dimensions[0].id: "gut"})],
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
```

- [ ] **Step 2: Run to verify failure** → FAIL (ImportError).

- [ ] **Step 3: Implement** (append to `touchstone/gui/judge_meta.py`)

```python
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
    out.extend(report_md.section_methode(
        pack=pack, include_judging=False, judge={}, reports=[],
        title_by=title_by, known_ids=known_ids))
    out.extend(report_md.section_dimensionen(pack))
    out.extend(report_md.section_prompt_varianten(pack, GLOSSARY))
    out.extend(report_md.section_prompts_antworten(
        pack=pack, responses=responses, verdicts=[], glossary=GLOSSARY,
        title_by=title_by, top=""))
    w("\n---\n\n# Teil A — Deine frische Bewertung (zuerst ausfüllen)\n")
    w(report_md._eval_task(pack, responses, prompt_link, known_ids))
    w("\n---\n\n# Teil B — Begründungen des lokalen Judges (erst jetzt lesen, dann kritisieren)\n")
    out.extend(report_md.section_master_scorecard(
        pack=pack, master_rows=master_rows, reports=reports, cited_ids=cited_ids,
        cells_by=cells_by, glossary=GLOSSARY, title_by=title_by, known_ids=known_ids))
    return "\n".join(out) + "\n"
```

- [ ] **Step 4: Run to verify pass** → `uv run pytest tests/test_judge_meta.py -q` → PASS.
- [ ] **Step 5: Type-check + lint + commit**

```bash
uv run mypy touchstone/gui/judge_meta.py && uv run ruff check touchstone/gui/judge_meta.py tests/test_judge_meta.py && uv run ruff format --check touchstone/gui/judge_meta.py tests/test_judge_meta.py
git add touchstone/gui/judge_meta.py tests/test_judge_meta.py
git commit -m "feat(judge_meta): render_request_md (Part A blank + Part B local, reuses E helpers)"
```

---

### Task 5: CLI `judge-meta export` / `ingest`

**Files:**
- Modify: `touchstone/cli.py`
- Test: `tests/test_cli_judge_meta.py`

**Interfaces:**
- Consumes (lazy-imported in command bodies): `touchstone.gui.bundles.bundle_detail`, `touchstone.gui.judge_meta.{render_request_md, empty_response_template, parse_meta_response, compute_agreement, aggregate_rubric, render_judge_quality_md}`.
- Produces: typer sub-app `judge_meta_app` registered as `app.add_typer(judge_meta_app, name="judge-meta")` with `export` + `ingest` commands.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_judge_meta.py
import json
from pathlib import Path

from typer.testing import CliRunner

from touchstone.cli import app
from touchstone.judge import write_reports_jsonl
from touchstone.pack import load_pack
from touchstone.results import ModelReport

PACK = "packs/ndassist.yaml"
runner = CliRunner()


def _judged_bundle(d: Path):
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    d.mkdir(parents=True)
    (d / "bundle.json").write_text(
        json.dumps({"pack_id": pk.id, "pack_path": PACK, "host": {"chip": "M5", "ram_gb": "64 GB"},
                    "date": "2026-06-24", "judge": {"model": "qwen3-27b"}}), encoding="utf-8")
    base = {"pack_id": pk.id, "pack_version": 1, "machine": "t", "model": "m", "quant": "q4",
            "engine": "lm-studio", "engine_version": "0", "variant": "baseline", "category": "A",
            "prompt_id": first.id, "repeat": 0, "response_text": "A.", "content_empty": False,
            "ttft_s": 0.2, "decode_tps": 30.0, "prefill_tps": 90.0, "e2e_s": 1.5, "prompt_tokens": 100,
            "completion_tokens": 50, "is_cold_start": False, "power_source": "ac", "peak_rss_mb": 0.0,
            "sys_used_mb": 20000.0, "mem_pressure_max": "normal", "throttled": False, "ok": True,
            "error": "", "seed": 42, "t_start": 0.0, "t_end": 1.5, "reasoning_chars": 0}
    (d / "responses.jsonl").write_text(json.dumps(base) + "\n", encoding="utf-8")
    hdr = "model,variant,metric_type,metric,weight,score"
    rows = [hdr] + [f"m,baseline,dimension,{dim.id},{dim.weight},4" for dim in pk.dimensions]
    (d / "scores.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    write_reports_jsonl(d / "reports.jsonl", [ModelReport(
        model="m", variant="baseline", dim_scores={dim.id: 4 for dim in pk.dimensions},
        dim_rationales={pk.dimensions[0].id: "gut"})])
    return pk


def test_export_writes_request_and_template(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    res = runner.invoke(app, ["judge-meta", "export", str(d)])
    assert res.exit_code == 0, res.output
    assert (d / "judge_meta_request.md").exists()
    assert (d / "judge_meta_response.yaml").exists()


def test_export_refuses_unjudged_bundle(tmp_path):
    pk = load_pack(PACK)
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps({"pack_id": pk.id, "pack_path": PACK, "host": {}, "date": "x"}), encoding="utf-8")
    (d / "responses.jsonl").write_text("", encoding="utf-8")  # no reports.jsonl → unjudged
    res = runner.invoke(app, ["judge-meta", "export", str(d)])
    assert res.exit_code != 0
    assert "judge" in res.output.lower()


def test_ingest_writes_judge_quality(tmp_path):
    d = tmp_path / "2026_eval_nd"
    pk = _judged_bundle(d)
    dims = "{" + ", ".join(f"{dim.id}: 4" for dim in pk.dimensions) + "}"
    crit = "\n".join(
        f"        {dim.id}: {{cites_evidence: true, names_improvement: true, justifies_level: true, catches_safety: true}}"
        for dim in pk.dimensions)
    (d / "judge_meta_response.yaml").write_text(
        "cells:\n  - model: m\n    variant: baseline\n"
        f"    fresh_scores: {{dimensions: {dims}, ko_fired: false, overall: Ja}}\n"
        f"    critique:\n      dimensions:\n{crit}\n      summary: 'ok'\n"
        "recommendations:\n  - 'Mehr Belege zitieren.'\n", encoding="utf-8")
    res = runner.invoke(app, ["judge-meta", "ingest", str(d)])
    assert res.exit_code == 0, res.output
    qa = (d / "judge_quality.md").read_text(encoding="utf-8")
    assert "## Headline" in qa and "Mehr Belege zitieren." in qa


def test_ingest_missing_response_errors(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    res = runner.invoke(app, ["judge-meta", "ingest", str(d)])
    assert res.exit_code != 0
```

- [ ] **Step 2: Run to verify failure** → FAIL (no `judge-meta` command).

- [ ] **Step 3: Implement** — add to `touchstone/cli.py`. Near the other `@app.command` definitions, add a sub-app and register it (place the registration after `app = typer.Typer(...)` block — define the sub-app object once, add commands, then `app.add_typer`):

```python
judge_meta_app = typer.Typer(add_completion=False, help="Meta-evaluate the local judge's quality.")
app.add_typer(judge_meta_app, name="judge-meta")


@judge_meta_app.command("export")
def judge_meta_export(
    bundle: Path = typer.Argument(..., exists=True, help="a judged eval bundle (run dir)"),
) -> None:
    """Write judge_meta_request.md + an empty judge_meta_response.yaml for an external cloud AI."""
    from touchstone.gui import bundles
    from touchstone.gui.judge_meta import empty_response_template, render_request_md

    detail = bundles.bundle_detail(bundle)
    if detail is None or not detail.get("reports"):
        typer.echo(
            "Dieses Bundle hat noch keine Judge-Bewertung (reports.jsonl). "
            "Bitte erst `touchstone judge` laufen lassen.",
            err=True,
        )
        raise typer.Exit(code=1)
    cells = sorted({(r.model, r.variant) for r in detail["reports"]})
    (bundle / "judge_meta_request.md").write_text(render_request_md(detail), encoding="utf-8")
    (bundle / "judge_meta_response.yaml").write_text(
        empty_response_template(detail["pack"], cells), encoding="utf-8"
    )
    typer.echo(
        f"Geschrieben: {bundle / 'judge_meta_request.md'} + judge_meta_response.yaml\n"
        "→ Request durch eine Cloud-KI jagen, judge_meta_response.yaml ausfüllen, dann "
        "`touchstone judge-meta ingest` laufen lassen."
    )


@judge_meta_app.command("ingest")
def judge_meta_ingest(
    bundle: Path = typer.Argument(..., exists=True, help="the bundle with a filled response"),
    response: Path | None = typer.Option(None, "--response", help="path to the filled YAML"),
) -> None:
    """Compute agreement + rationale-quality rubric → judge_quality.md."""
    from touchstone.gui import bundles
    from touchstone.gui.judge_meta import (
        aggregate_rubric,
        compute_agreement,
        parse_meta_response,
        render_judge_quality_md,
    )

    rpath = response or (bundle / "judge_meta_response.yaml")
    if not rpath.exists():
        typer.echo(f"Keine Response-Datei: {rpath} (erst `judge-meta export` + ausfüllen).", err=True)
        raise typer.Exit(code=1)
    detail = bundles.bundle_detail(bundle)
    if detail is None or not detail.get("reports"):
        typer.echo("Bundle ohne Judge-Bewertung — `touchstone judge` zuerst.", err=True)
        raise typer.Exit(code=1)
    try:
        meta = parse_meta_response(rpath.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — surface a clean message, never a stacktrace
        typer.echo(f"judge_meta_response.yaml ungültig: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    agreement = compute_agreement(detail["pack"], detail["reports"], detail["master_rows"], meta)
    rubric = aggregate_rubric(detail["reports"], meta)
    md = render_judge_quality_md(detail, agreement, rubric, meta)
    (bundle / "judge_quality.md").write_text(md, encoding="utf-8")
    typer.echo(f"Geschrieben: {bundle / 'judge_quality.md'}")
```

- [ ] **Step 4: Run to verify pass** → `uv run pytest tests/test_cli_judge_meta.py -q` → PASS (4 tests).
- [ ] **Step 5: Full suite + type-check + lint + commit**

```bash
uv run pytest -q && uv run mypy touchstone/ && uv run ruff check . && uv run ruff format --check .
git add touchstone/cli.py tests/test_cli_judge_meta.py
git commit -m "feat(cli): judge-meta export/ingest (lazy-imports gui; refuses unjudged bundles)"
```

---

### Task 6: `judge_quality.md` in the export allowlist

**Files:**
- Modify: `touchstone/gui/app.py` (`_LEDGER` ~line 636, single-file `allowed` set ~line 448)
- Test: `tests/test_gui_judge_quality_export.py`

**Interfaces:**
- Consumes: existing `/export/{name}/{fname}` route + `/runs/batch-export` `_LEDGER`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_judge_quality_export.py
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry


class _L:
    def spawn(self, a): return 1
    def alive(self, p): return False
    def terminate(self, p): return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_L())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def test_judge_quality_is_single_file_exportable(tmp_path):
    d = tmp_path / "2026_eval_nd"
    d.mkdir()
    (d / "judge_quality.md").write_text("# Judge-Qualität\n", encoding="utf-8")
    r = _client(tmp_path).get(f"/export/{d.name}/judge_quality.md")
    assert r.status_code == 200
    assert "Judge-Qualität" in r.text
```

- [ ] **Step 2: Run to verify failure** → FAIL (judge_quality.md not in `allowed` → 404).

- [ ] **Step 3: Implement** — in `touchstone/gui/app.py`:
  - add `"judge_quality.md"` to the single-file `allowed` set (~line 448):
    `allowed = {"scorecard.md", "scores.csv", "perf.csv", "report.md", "aggregate.md", "judge_quality.md"}`
  - add `"judge_quality.md"` to the `_LEDGER` list (~line 636) so batch-export includes it.

- [ ] **Step 4: Run to verify pass** → `uv run pytest tests/test_gui_judge_quality_export.py -q` → PASS.
- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/app.py tests/test_gui_judge_quality_export.py
git commit -m "feat(gui): judge_quality.md in export allowlist (single-file + batch)"
```

---

### Task 7: Full verification + headless smoke

**Files:** none (verification only).

- [ ] **Step 1: Full gate**

Run: `uv run pytest -q && uv run mypy touchstone/ && uv run ruff check . && uv run ruff format --check .`
Expected: all green/clean (577 baseline + new judge_meta/cli/export tests).

- [ ] **Step 2: Headless end-to-end smoke** (real judged bundle → export → fill → ingest)

```bash
uv run python - <<'PY'
import json, tempfile
from pathlib import Path
from touchstone.pack import load_pack
from touchstone.judge import write_reports_jsonl
from touchstone.results import ModelReport
from touchstone.gui import bundles
from touchstone.gui.judge_meta import (empty_response_template, render_request_md,
    parse_meta_response, compute_agreement, aggregate_rubric, render_judge_quality_md)

PACK="packs/ndassist.yaml"; pk=load_pack(PACK); first=next(p for _,p in pk.all_prompts())
d=Path(tempfile.mkdtemp())/"2026_eval_nd"; d.mkdir(parents=True)
(d/"bundle.json").write_text(json.dumps({"pack_id":pk.id,"pack_path":PACK,
    "host":{"chip":"M5","ram_gb":"64 GB"},"date":"2026-06-24","judge":{"model":"qwen3-27b"}}))
base={"pack_id":pk.id,"pack_version":1,"machine":"t","model":"m","quant":"q4","engine":"lm-studio",
 "engine_version":"0","variant":"baseline","category":"A","prompt_id":first.id,"repeat":0,
 "response_text":"A.","content_empty":False,"ttft_s":0.2,"decode_tps":30.0,"prefill_tps":90.0,
 "e2e_s":1.5,"prompt_tokens":100,"completion_tokens":50,"is_cold_start":False,"power_source":"ac",
 "peak_rss_mb":0.0,"sys_used_mb":20000.0,"mem_pressure_max":"normal","throttled":False,"ok":True,
 "error":"","seed":42,"t_start":0.0,"t_end":1.5,"reasoning_chars":0}
(d/"responses.jsonl").write_text(json.dumps(base)+"\n")
hdr="model,variant,metric_type,metric,weight,score"
(d/"scores.csv").write_text("\n".join([hdr]+[f"m,baseline,dimension,{dim.id},{dim.weight},4" for dim in pk.dimensions])+"\n")
write_reports_jsonl(d/"reports.jsonl",[ModelReport(model="m",variant="baseline",
    dim_scores={dim.id:4 for dim in pk.dimensions},dim_rationales={pk.dimensions[0].id:"gut"})])
detail=bundles.bundle_detail(d)
(d/"judge_meta_request.md").write_text(render_request_md(detail))
cells=sorted({(r.model,r.variant) for r in detail["reports"]})
tmpl=empty_response_template(detail["pack"],cells)
# fill template: cloud gives Q* = 4 fresh + all rubric checks true
import yaml
data=yaml.safe_load(tmpl)
for c in data["cells"]:
    c["fresh_scores"]["dimensions"]={dim.id:4 for dim in pk.dimensions}
    c["fresh_scores"]["overall"]="Ja"
    for dd in c["critique"]["dimensions"].values():
        dd.update(cites_evidence=True,names_improvement=True,justifies_level=True,catches_safety=True)
data["recommendations"]=["Bei Score < 5 benennen, was besser wäre."]
meta=parse_meta_response(yaml.safe_dump(data,allow_unicode=True))
agg=compute_agreement(detail["pack"],detail["reports"],detail["master_rows"],meta)
rub=aggregate_rubric(detail["reports"],meta)
qa=render_judge_quality_md(detail,agg,rub,meta)
assert "## Headline" in qa and "## 1. Agreement" in qa and "## 3. Empfohlene" in qa
assert "Bei Score < 5" in qa
assert "Teil A" in (d/"judge_meta_request.md").read_text() and "## Master-Scorecard" in (d/"judge_meta_request.md").read_text()
print("SMOKE OK · request", len((d/"judge_meta_request.md").read_text()), "bytes · quality", len(qa), "bytes")
PY
```
Expected: `SMOKE OK · …`.

- [ ] **Step 3: Final commit (if verification fixups were needed)**

```bash
git add -A && git commit -m "test(judge_meta): full-suite + headless e2e smoke green" || echo "nothing to commit"
```

---

## Post-plan (controller, not a subagent task)

- **Adversariale Whole-Branch-Review** (opus): (1) Bias-Schutz — Teil A enthält keine lokalen
  Score-Zahlen; (2) Agreement-Mathematik (Quality% über `max_weighted`, Δ, ko-Konkordanz); (3)
  `names_improvement`-n/a-Logik (lokaler Score==5 nie im Nenner). Controller verifiziert jeden Fund.
- **Merge:** `feat/judge-quality-meta-eval` → `main`, Push (Codeberg→GitHub). Kein PR (Solo-Repo).

## Self-Review (plan author)

- **Spec coverage:** CLI export/ingest (T5) · request doc Part A/B + ordering (T4) · response schema +
  parse + template (T1) · agreement math + rubric incl. names_improvement<5 (T2) · judge_quality.md
  report w/ headline+3 sections (T3) · refusal without judging (T5) · export allowlist (T6) · tests +
  smoke (T1-T7). All spec sections mapped.
- **Placeholders:** none — every code step carries complete code; report_md reuse uses the exact
  helper signatures from sub-project E (verified present at report_md.py:114/168/274/285/300/346/450).
- **Type consistency:** `MetaResponse`/`CellFreshScores`/`DimCritique`/`MetaCell`, `compute_agreement`,
  `aggregate_rubric`, `RubricSummary`, `render_judge_quality_md`, `render_request_md`,
  `empty_response_template`, `parse_meta_response` used identically across tasks. `pack.max_weighted`
  and `ModelReport.dim_scores` match the confirmed source. CLI lazy-imports the gui module (boundary
  respected).
