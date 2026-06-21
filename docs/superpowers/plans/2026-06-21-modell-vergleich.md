# Modell-Vergleich (Ink. 8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine kontrollierte Vergleichs-Ansicht `/compare/{bundle}?axis=model|variant` innerhalb eines Bundles, die **Leistung = Qualität in Relation zu Kosten** (Speed + RAM + CPU) über drei Schichten zeigt (Effizienz-Scatter · Kopf-an-Kopf · per-Aufgabe-Drill-down).

**Architecture:** Neues, pures `ramcheck/gui/compare.py` lädt ein Bundle über das bestehende `bundles.bundle_detail` (das den `master_rows`⨝`reports`-Join schon macht), gruppiert per `scorecard.model_variant_groups` entlang einer Achse (die andere Dimension wird auf einen Wert projiziert → maximal kontrolliert), berechnet Speed/RAM direkt aus den `EvalResponse`-Feldern und CPU GUI-seitig über die `[t_start,t_end]`-Fenster aus `resources.jsonl`. Eine neue Route + `compare_axis.html` + `scatter.js` rendern es; `result.html`/`overview.html` bekommen einen „↔ Vergleichen"-Link. Kein Datenmodell-Umbau, keine Änderung an `EvalResponse`/`merge`.

**Tech Stack:** Python 3.12 · FastAPI · Jinja2 · pydantic-Pack · dataclasses · pytest (`uv run pytest`) · build-freies Inline-SVG-JS.

---

## Pre-flight (für den ausführenden Worker)

- **Working dir:** `/Users/Shared/code/llm-benchmark-harness`. Tests laufen mit `uv run pytest` aus dem Repo-Root (cwd-relative `packs/ndassist.yaml` muss auflösen — das tut es vom Root).
- **Gates nach jeder Task:** `uv run pytest <neue testdatei> -q`, am Ende Task 10: `uv run pytest -q` (alle), `uv run mypy ramcheck`, `uv run ruff check ramcheck tests`, `uv run ruff format --check ramcheck tests`.
- **Commit-Trailer:** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Branch:** `feat/modell-vergleich` ist bereits ausgecheckt.

## Verifizierte Fakten (aus der Codebase-Map — keine Annahmen)

- `scorecard.model_variant_groups(responses) -> list[tuple[str,str]]` — eindeutige `(model,variant)` in First-seen-Reihenfolge, funktioniert un-judged.
- `bundles.bundle_detail(run_dir) -> dict | None` liefert u. a. `responses` (`list[EvalResponse]`), `verdicts` (`list[Verdict]`), `reports` (`list[ModelReport]`), `master_rows` (`list[dict]` mit `model,variant,pct,safety_passed,safety_reason,recommendation` — **trägt KEIN dim_scores**), `pack` (`Pack`). Gibt `None` zurück, wenn der Pack fehlt.
- `ModelReport`: `model,variant, dim_scores: dict[str,int], dim_rationales: dict[str,str]`.
- `EvalResponse`-Felder (relevant): `model,variant,prompt_id,repeat,category, decode_tps,ttft_s,e2e_s, sys_used_mb: float|None, mem_pressure_max: str, peak_rss_mb: float|None, is_cold_start, ok, t_start, t_end, response_text, content_empty, reasoning_chars`. **Kein `cpu_pct`.**
- `Verdict`: `model,variant,prompt_id,repeat,category,score:int,red_flag:bool,rationale:str,unscored:bool=False`.
- `models.ResourceSample`: `ts,sys_used_mb,sys_available_mb,swap_used_mb,server_rss_mb,mem_pressure_level,throttled, cpu_pct: float|None = None`.
- `merge.load_samples_jsonl(path) -> list[ResourceSample]` (line-tolerant). `merge.DEFAULT_TOLERANCE_S = 0.5`. Fenster-Muster: `t_start - tol <= s.ts <= t_end + tol`.
- `models.pressure_max(levels: list[str]) -> str` (leer → `"normal"`).
- `stats.median(values: list[float]) -> float`, `stats.percentile(values: list[float], pct: float) -> float`.
- `Pack` (Pydantic): `.dimensions` (`list[Dimension]` mit `.id,.name,.weight`), `.ko_rule` (`.dimension,.threshold,.red_flag_prompts`), `.categories`, `.all_prompts() -> list[tuple[Category,PackPrompt]]`, `.max_weighted() -> int`. `PackPrompt`: `.id,.title,.prompt,.safety_critical,.repeats`.
- `ndassist.yaml`: dims Q1(3),Q2(2),Q3(2),Q4(2),Q5(3),Q6(3),Q7(1) → `max_weighted=80`; `ko_rule` Q6 ≤ 2, red_flag `[E1]`; Varianten `baseline`/`none`; Prompts A1..A5,B1..B5,C1..C5,D1..D4,E1..E5.
- App: `create_app(*, runs_dir, registry)`, Modul-`render(name, request, **ctx)`, `_templates` (Jinja2, dir `ramcheck/gui/templates`), `/static` mount. `/result/{name}` confined via `(runs_dir/name).resolve()` + `is_relative_to`. Sidebar in `base.html`: „1 · Übersicht", „3 · Konfig + Start", „6 · Vergleich" (= cross-run `/compare`).
- Test-Muster: `gui_app.create_app(runs_dir=tmp_path, registry=RunRegistry(runs_dir=tmp_path, launcher=Fake))`; `RunRegistry` aus `ramcheck.gui.control`. Pack-Konstante in Tests: `PACK = "packs/ndassist.yaml"`. `write_reports_jsonl` aus `ramcheck.judge`.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `ramcheck/gui/compare.py` *(neu)* | Pure Read/Aggregations-Schicht: `CompareDetail`/`CompareCell`/`DivergencePrompt`/`CellAnswer`/`AxisOptions`-Dataclasses, `compare_detail()`, `axis_options()`, `axis_options_for_dir()` + Helfer `_cpu_for_window`, `_cell_metrics`, `_relations_summary`, `_winners`, `_divergence`. |
| `ramcheck/gui/app.py` *(ändern)* | Route `/compare/{name}` (confined, `axis`/Projektion validiert); `/result`- und `/`-Route reichen Vergleichs-Link-Daten an die Templates. |
| `ramcheck/gui/templates/compare_axis.html` *(neu)* | 3-Schichten-Ansicht. |
| `ramcheck/gui/static/scatter.js` *(neu)* | Build-freier Inline-SVG-Scatter (x=Decode, y=Qualität, r=Peak-RAM). |
| `ramcheck/gui/templates/result.html` *(ändern)* | „↔ Vergleichen"-Link (nur bei >1 Achsenwert). |
| `ramcheck/gui/templates/overview.html` *(ändern)* | „↔ Vergleichen"-Link je Zeile (nur bei >1 Achsenwert). |
| `tests/test_gui_compare.py` *(neu)* | Pure-Unit-Tests + Fixture-Builder `_write_compare_bundle`, `_two_model_bundle`. |
| `tests/test_gui_compare_route.py` *(neu)* | Route-/Render-Tests via `TestClient`. |
| `AGENTS.md` *(ändern)* | `/compare/{bundle}` (Innerhalb-Bundle-Achsen-Vergleich) vs. `/compare` (Cross-Run-Aggregat) abgrenzen. |

---

## Task 1: `compare.py` — Dataclasses + `axis_options` (pure)

**Files:**
- Create: `ramcheck/gui/compare.py`
- Test: `tests/test_gui_compare.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_compare.py
from __future__ import annotations

from ramcheck.gui import compare
from ramcheck.results import EvalResponse


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare.py -q`
Expected: FAIL — `AttributeError: module 'ramcheck.gui.compare' has no attribute 'axis_options'` (module doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

```python
# ramcheck/gui/compare.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/compare.py tests/test_gui_compare.py
git commit -m "feat(compare): axis_options + module scaffold for within-bundle compare"
```

---

## Task 2: `_cpu_for_window` (pure, V6) — CPU aus resources.jsonl-Fenstern

**Files:**
- Modify: `ramcheck/gui/compare.py`
- Test: `tests/test_gui_compare.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gui_compare.py
from ramcheck.models import ResourceSample


def _sample(ts: float, cpu: float | None) -> ResourceSample:
    return ResourceSample(
        ts=ts, sys_used_mb=1.0, sys_available_mb=1.0, swap_used_mb=0.0,
        server_rss_mb=None, mem_pressure_level="normal", throttled=False, cpu_pct=cpu,
    )


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare.py -k cpu_for_window -q`
Expected: FAIL — `AttributeError: ... has no attribute '_cpu_for_window'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to ramcheck/gui/compare.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare.py -k cpu_for_window -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/compare.py tests/test_gui_compare.py
git commit -m "feat(compare): _cpu_for_window with first-class 'n. v.' state (V6)"
```

---

## Task 3: Fixture-Builder + `compare_detail` Kern (Qualität-Join + Speed/RAM + single-state)

**Files:**
- Modify: `ramcheck/gui/compare.py`
- Test: `tests/test_gui_compare.py`

- [ ] **Step 1: Write the failing test (incl. hermetic bundle builder)**

```python
# append to tests/test_gui_compare.py
import json

from ramcheck.judge import write_reports_jsonl
from ramcheck.pack import load_pack
from ramcheck.results import ModelReport, Verdict

PACK = "packs/ndassist.yaml"


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


def test_compare_detail_variant_axis_quality_and_speed():
    import pathlib
    d = _two_variant_bundle(pathlib.Path(_mk_runs(__import__("tempfile").mkdtemp())))
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
    import pathlib, tempfile
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_one"
    _write_compare_bundle(
        d, cells=[("m", "baseline")],
        dim_scores_by_cell={("m", "baseline"): {q: 4 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]}},
    )
    detail = compare.compare_detail(d, "model")  # only 1 model
    assert detail is not None
    assert detail.single is True
    assert len(detail.cells) == 1


def _mk_runs(p):
    return p
```

> Note: `_mk_runs` is a trivial shim so the builder accepts a `Path`; keep tests using `tempfile.mkdtemp()` (no `tmp_path` fixture needed for pure tests, but you MAY switch them to `tmp_path` — adjust signatures accordingly). The route tests in Task 7 use `tmp_path` + `TestClient`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare.py -k compare_detail -q`
Expected: FAIL — `AttributeError: ... has no attribute 'compare_detail'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to ramcheck/gui/compare.py
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
        divergence=[],                   # Task 5
        judged=judged, single=len(cells) <= 1,
        pack=pk, dimensions=list(pk.dimensions), run_name=run_dir.name,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare.py -k "compare_detail or cpu or axis_options" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/compare.py tests/test_gui_compare.py
git commit -m "feat(compare): compare_detail core — master⨝reports quality + speed/RAM per axis value"
```

---

## Task 4: `_relations_summary` + `_winners` + `scatter_points`

**Files:**
- Modify: `ramcheck/gui/compare.py`
- Test: `tests/test_gui_compare.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gui_compare.py
import pathlib, tempfile


def test_relations_summary_states_numbers_and_quality_leader():
    d = _two_variant_bundle(pathlib.Path(tempfile.mkdtemp()))
    detail = compare.compare_detail(d, "variant")
    s = detail.relations_summary
    assert "baseline" in s and "none" in s
    assert "80" in s and "40" in s            # the two quality %s
    assert "Prozentpunkte" in s               # quality-leader clause
    # descriptive only: no hard recommendation verb
    assert "empfehl" not in s.lower()


def test_winners_per_row():
    d = _two_variant_bundle(pathlib.Path(tempfile.mkdtemp()))
    detail = compare.compare_detail(d, "variant")
    assert detail.winners["pct"] == "baseline"        # higher quality
    assert detail.winners["decode"] == "none"         # 14 > 12 tok/s
    assert detail.winners["ttft"] == "none"           # lower TTFT wins
    assert detail.winners["ram"] == "none"            # lower peak RAM wins
    assert detail.winners["Q6"] == "baseline"         # 4 > 2


def test_scatter_points_only_rated_cells():
    d = _two_variant_bundle(pathlib.Path(tempfile.mkdtemp()))
    detail = compare.compare_detail(d, "variant")
    pts = {p["label"]: p for p in detail.scatter_points}
    assert pts["baseline"]["x"] == 12.0
    assert round(pts["baseline"]["y"]) == 80
    assert pts["baseline"]["r"] == 8200.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare.py -k "relations or winners or scatter" -q`
Expected: FAIL — `relations_summary` is `""`, `winners` is `{}`, `scatter_points` is `[]`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to ramcheck/gui/compare.py (helpers) and wire into compare_detail
def _relations_summary(cells: list[CompareCell], axis_label: str) -> str:
    """Descriptive relation in words (names the numbers, no hard recommendation)."""
    rated = [c for c in cells if c.pct is not None]
    if len(rated) < 2:
        return f"Nicht genug bewertete {axis_label}-Werte für eine Relation."

    def clause(c: CompareCell) -> str:
        speed = f"{c.decode_tps:.0f} tok/s" if c.decode_tps is not None else "Speed n. v."
        ram = f"{c.peak_ram_mb / 1024:.1f} GB Peak-RAM" if c.peak_ram_mb is not None else "RAM n. v."
        return f"{c.label}: {c.pct:.0f} % Qualität bei {speed} und {ram}"

    body = "; ".join(clause(c) for c in rated)
    best_q = max(rated, key=lambda c: c.pct)
    others = [c for c in rated if c.label != best_q.label]
    bits: list[str] = []
    if others:
        nearest = max(others, key=lambda c: c.pct)
        bits.append(
            f"{best_q.label} führt bei der Qualität "
            f"(+{best_q.pct - nearest.pct:.0f} Prozentpunkte ggü. {nearest.label})"
        )
    speed_cells = [c for c in rated if c.decode_tps is not None]
    if speed_cells:
        fastest = max(speed_cells, key=lambda c: c.decode_tps)
        if fastest.label == best_q.label:
            bits.append(f"{best_q.label} ist zugleich am schnellsten")
        else:
            bits.append(f"{fastest.label} ist schneller ({fastest.decode_tps:.0f} tok/s)")
    tail = (". ".join(bits) + ".") if bits else ""
    return f"{body}. {tail}".strip()


def _winners(cells: list[CompareCell], dimensions: list) -> dict[str, str | None]:
    def best(getter, higher: bool) -> str | None:
        scored = [(getter(c), c.label) for c in cells if getter(c) is not None]
        if not scored:
            return None
        return (max if higher else min)(scored, key=lambda t: t[0])[1]

    w: dict[str, str | None] = {
        "pct": best(lambda c: c.pct, True),
        "decode": best(lambda c: c.decode_tps, True),
        "ttft": best(lambda c: c.ttft_p50, False),
        "e2e": best(lambda c: c.e2e_med, False),
        "ram": best(lambda c: c.peak_ram_mb, False),
        "cpu": best(lambda c: c.cpu_max, False),
    }
    for d in dimensions:
        w[d.id] = best(lambda c, did=d.id: c.dim_scores.get(did), True)
    return w


def _scatter_points(cells: list[CompareCell]) -> list[dict]:
    return [
        {"label": c.label, "x": c.decode_tps, "y": c.pct, "r": c.peak_ram_mb}
        for c in cells
        if c.decode_tps is not None and c.pct is not None
    ]
```

Then, in `compare_detail`, replace the three placeholder fields:

```python
    return CompareDetail(
        ...
        relations_summary=_relations_summary(cells, axis_label),
        winners=_winners(cells, list(pk.dimensions)),
        scatter_points=_scatter_points(cells),
        ...
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare.py -k "relations or winners or scatter" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/compare.py tests/test_gui_compare.py
git commit -m "feat(compare): relations summary, per-row winners, scatter points"
```

---

## Task 5: Divergenz (③, per-Prompt `Verdict.score`-Δ, V7)

**Files:**
- Modify: `ramcheck/gui/compare.py`
- Test: `tests/test_gui_compare.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gui_compare.py
def _verdict(model, variant, prompt_id, score, *, repeat=0, red_flag=False, rationale="r", category="A"):
    return Verdict(model=model, variant=variant, prompt_id=prompt_id, repeat=repeat,
                   category=category, score=score, red_flag=red_flag, rationale=rationale)


def test_divergence_sorted_by_abs_delta_and_means_repeats():
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_div"
    _write_compare_bundle(
        d,
        cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]},
            ("m", "none"): {q: 3 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]},
        },
        verdicts_by_cell={
            ("m", "baseline"): [_verdict("m", "baseline", "A1", 5), _verdict("m", "baseline", "A2", 4),
                                _verdict("m", "baseline", "A2", 2, repeat=1)],
            ("m", "none"): [_verdict("m", "none", "A1", 2), _verdict("m", "none", "A2", 3),
                            _verdict("m", "none", "A2", 3, repeat=1)],
        },
    )
    detail = compare.compare_detail(d, "variant")
    div = detail.divergence
    assert div[0].prompt_id == "A1"          # |5-2| = 3 is the biggest gap, comes first
    assert div[0].scores["baseline"] == 5.0
    assert div[0].scores["none"] == 2.0
    assert div[0].delta == 3.0
    a2 = next(p for p in div if p.prompt_id == "A2")
    assert a2.scores["baseline"] == 3.0      # mean(4,2) over repeats
    assert a2.delta == 0.0
    # answers carry the per-cell response_text + verdict
    ans = {a.label: a for a in div[0].answers}
    assert ans["baseline"].score == 5
    assert ans["none"].score == 2


def test_divergence_empty_when_unjudged():
    d = pathlib.Path(tempfile.mkdtemp()) / "2026_eval_unj"
    _write_compare_bundle(
        d, cells=[("m", "baseline"), ("m", "none")],
        dim_scores_by_cell={
            ("m", "baseline"): {q: 4 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]},
            ("m", "none"): {q: 4 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]},
        },
    )
    detail = compare.compare_detail(d, "variant")
    assert detail.divergence == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare.py -k divergence -q`
Expected: FAIL — `divergence` is `[]` even when verdicts exist.

- [ ] **Step 3: Write minimal implementation**

```python
# append to ramcheck/gui/compare.py
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
```

Wire it into `compare_detail` (need `verdicts` from `base`):

```python
    verdicts = base["verdicts"]
    ...
    divergence=_divergence(cells, responses, verdicts, pk),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare.py -q`
Expected: PASS (all compare unit tests).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/compare.py tests/test_gui_compare.py
git commit -m "feat(compare): per-prompt divergence (V7) with repeat-mean + side-by-side answers"
```

---

## Task 6: `axis=model`-Projektion (synthetische 2-Modell×2-Varianten-Fixture)

**Files:**
- Modify: `tests/test_gui_compare.py` (Fixture + Tests; impl already supports it)
- Test: `tests/test_gui_compare.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gui_compare.py
def _two_model_bundle(tmp_dir):
    """Synthetic 2-model × 2-variant bundle (no real bundle covers axis=model)."""
    d = pathlib.Path(tmp_dir) / "2026_eval_2x2"
    full = {q: 4 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]}
    _write_compare_bundle(
        d,
        cells=[("alpha", "baseline"), ("alpha", "none"), ("beta", "baseline"), ("beta", "none")],
        dim_scores_by_cell={
            ("alpha", "baseline"): full, ("alpha", "none"): {**full, "Q6": 2},
            ("beta", "baseline"): {q: 5 for q in full}, ("beta", "none"): full,
        },
        perf_by_cell={
            ("alpha", "baseline"): {"decode_tps": 10.0}, ("alpha", "none"): {"decode_tps": 11.0},
            ("beta", "baseline"): {"decode_tps": 20.0}, ("beta", "none"): {"decode_tps": 21.0},
        },
    )
    return d


def test_axis_model_projects_baseline_by_default():
    d = _two_model_bundle(tempfile.mkdtemp())
    detail = compare.compare_detail(d, "model")     # default projection -> baseline
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
    assert alpha.recommendation == "Nein"          # alpha/none has Q6=2 -> K.-o.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare.py -k axis_model -q`
Expected: FAIL — assertion errors only if projection logic is wrong; if Task 3 was correct these may pass immediately. If they pass, that is acceptable (impl already supports it) — proceed to commit. If they fail, fix `compare_detail`'s projection branch until green.

- [ ] **Step 3: Implementation**

No new code expected (Task 3 implements projection). If a test fails, the bug is in the `resolved == "model"` branch of `compare_detail` — fix there.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare.py -k axis_model -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_gui_compare.py
git commit -m "test(compare): axis=model projection via synthetic 2×2 fixture"
```

---

## Task 7: Route `/compare/{name}` + `axis_options_for_dir`

**Files:**
- Modify: `ramcheck/gui/compare.py` (add `axis_options_for_dir`)
- Modify: `ramcheck/gui/app.py` (route)
- Test: `tests/test_gui_compare_route.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_compare_route.py
from __future__ import annotations

from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunRegistry

from test_gui_compare import _two_variant_bundle, _two_model_bundle, _write_compare_bundle


class _FakeLauncher:
    def spawn(self, argv):
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def test_compare_route_variant_axis_renders(tmp_path):
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=variant")
    assert r.status_code == 200
    body = r.text
    assert "baseline" in body and "none" in body
    assert "data-scatter" in body            # ① scatter
    assert "Prozentpunkte" in body           # relations fazit
    assert "n. v." in body                   # CPU column empty


def test_compare_route_model_axis_shows_projection(tmp_path):
    d = _two_model_bundle(tmp_path)
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=model")
    assert r.status_code == 200
    assert "Variante: baseline" in r.text     # projected variant labeled


def test_compare_route_bad_axis_422(tmp_path):
    d = _two_variant_bundle(tmp_path)
    assert _client(tmp_path).get(f"/compare/{d.name}?axis=bogus").status_code == 422


def test_compare_route_traversal_404(tmp_path):
    assert _client(tmp_path).get("/compare/..%2f..%2fetc").status_code == 404


def test_compare_route_single_axis_value_message(tmp_path):
    d = tmp_path / "2026_eval_one"
    _write_compare_bundle(
        d, cells=[("m", "baseline")],
        dim_scores_by_cell={("m", "baseline"): {q: 4 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]}},
    )
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=model")
    assert r.status_code == 200
    assert "nichts zu vergleichen" in r.text.lower()


def test_compare_route_unjudged_perf_only(tmp_path):
    # un-judged: reports.jsonl absent + no scores.csv dims -> no quality, perf only
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    import json
    from test_gui_compare import _resp
    (d / "bundle.json").write_text(json.dumps({
        "pack_id": "ndassist", "pack_path": "packs/ndassist.yaml",
        "models": [{"id": "m", "quant": "q"}], "date": "2026-06-20", "host": {"machine": "t"},
    }), encoding="utf-8")
    (d / "responses.jsonl").write_text(
        json.dumps(_resp("m", "baseline").as_dict()) + "\n"
        + json.dumps(_resp("m", "none").as_dict()) + "\n",
        encoding="utf-8",
    )
    r = _client(tmp_path).get(f"/compare/{d.name}?axis=variant")
    assert r.status_code == 200
    assert "noch nicht bewertet" in r.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare_route.py -q`
Expected: FAIL — 404 for `/compare/{name}` (route not registered) and missing `axis_options_for_dir`.

- [ ] **Step 3a: Add `axis_options_for_dir` to compare.py**

```python
# append to ramcheck/gui/compare.py
def axis_options_for_dir(run_dir: Path) -> AxisOptions:
    """Robustly load responses.jsonl and report axis options (for overview links)."""
    from ramcheck.qualrun import load_responses_jsonl

    try:
        responses = load_responses_jsonl(run_dir / "responses.jsonl")
    except Exception:
        responses = []
    return axis_options(responses)
```

- [ ] **Step 3b: Register the route in app.py**

In `ramcheck/gui/app.py`, ensure `compare` is imported (top of file alongside `bundles`):

```python
from ramcheck.gui import bundles, compare
```

Add the route next to the existing `/compare` handler (the cross-run aggregate stays unchanged):

```python
    @app.get("/compare/{name}", response_class=HTMLResponse)
    def compare_axis(
        request: Request,
        name: str,
        axis: str | None = None,
        variant: str | None = None,
        model: str | None = None,
    ) -> HTMLResponse:
        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
            raise HTTPException(status_code=404)
        if axis is not None and axis not in ("model", "variant"):
            raise HTTPException(status_code=422)
        projection = variant or model  # generated links only ever set the axis-relevant one
        try:
            detail = compare.compare_detail(rd, axis, projection=projection)
        except Exception:
            detail = None
        return render("compare_axis.html", request, detail=detail, run_dir=rd, active="overview")
```

> Note on import location: `bundles`/`compare` are already imported at module top in app.py (see existing `bundles` usage). If `compare` is not yet imported, add it as shown. The traversal-404 test passes because `..%2f..` resolves outside `runs_dir`.

- [ ] **Step 4: Run test to verify it passes (needs Task 8 template first)**

The route returns `render("compare_axis.html", ...)`, which fails until Task 8 creates that template. Therefore: implement Task 8 next, then run:

Run: `uv run pytest tests/test_gui_compare_route.py -q`
Expected: PASS after Task 8.

- [ ] **Step 5: Commit (after Task 8 green)**

```bash
git add ramcheck/gui/app.py ramcheck/gui/compare.py tests/test_gui_compare_route.py
git commit -m "feat(compare): /compare/{name} route + axis_options_for_dir"
```

---

## Task 8: Template `compare_axis.html` + `scatter.js`

**Files:**
- Create: `ramcheck/gui/templates/compare_axis.html`
- Create: `ramcheck/gui/static/scatter.js`
- Test: `tests/test_gui_compare_route.py` (already written in Task 7; this turns it green)

- [ ] **Step 1: Tests already exist (Task 7).** Run to confirm RED (template missing):

Run: `uv run pytest tests/test_gui_compare_route.py -q`
Expected: FAIL — `jinja2.exceptions.TemplateNotFound: compare_axis.html`.

- [ ] **Step 2: Create `scatter.js`**

```javascript
// ramcheck/gui/static/scatter.js
/**
 * scatter.js — build-free inline-SVG efficiency scatter for the Modell-Vergleich.
 *
 * x = Decode tok/s, y = Qualität %, r = Peak-RAM (sys_used_mb). One point per axis
 * value. Reads svg[data-scatter] with data-points = JSON [{label,x,y,r}, ...].
 * Mirrors sparkline.js (vanilla DOM, no deps, DOMContentLoaded wiring).
 */
"use strict";

(function () {
  var SVGNS = "http://www.w3.org/2000/svg";

  function el(name, attrs) {
    var e = document.createElementNS(SVGNS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function renderScatter(svg) {
    var pts = [];
    try { pts = JSON.parse(svg.dataset.points || "[]"); } catch (e) {}
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (pts.length === 0) return;

    var W = parseFloat(svg.getAttribute("width") || "320");
    var H = parseFloat(svg.getAttribute("height") || "220");
    var PAD = 38;

    var xs = pts.map(function (p) { return p.x; });
    var xMin = Math.min.apply(null, xs), xMax = Math.max.apply(null, xs);
    if (xMin === xMax) { xMin -= 1; xMax += 1; }
    var yMin = 0, yMax = 100;                              // quality is a fixed 0..100 %
    var rs = pts.map(function (p) { return p.r || 0; });
    var rMax = Math.max.apply(null, rs) || 1;

    function sx(x) { return PAD + (x - xMin) / (xMax - xMin) * (W - 2 * PAD); }
    function sy(y) { return (H - PAD) - (y - yMin) / (yMax - yMin) * (H - 2 * PAD); }
    function sr(r) { return 5 + (r / rMax) * 13; }         // 5..18 px radius

    // axes
    svg.appendChild(el("line", { x1: PAD, y1: H - PAD, x2: W - PAD, y2: H - PAD, stroke: "#d4d4d8", "stroke-width": 1 }));
    svg.appendChild(el("line", { x1: PAD, y1: PAD, x2: PAD, y2: H - PAD, stroke: "#d4d4d8", "stroke-width": 1 }));
    var xlab = el("text", { x: W / 2, y: H - 6, "text-anchor": "middle", "font-size": 10, fill: "#71717a" });
    xlab.textContent = "Decode tok/s →"; svg.appendChild(xlab);
    var ylab = el("text", { x: 10, y: PAD - 10, "font-size": 10, fill: "#71717a" });
    ylab.textContent = "Qualität %"; svg.appendChild(ylab);

    pts.forEach(function (p) {
      var cx = sx(p.x), cy = sy(p.y);
      svg.appendChild(el("circle", {
        cx: cx, cy: cy, r: sr(p.r || 0),
        fill: "rgba(37,99,235,0.25)", stroke: "#2563eb", "stroke-width": 1.5,
      }));
      var t = el("text", { x: cx, y: cy - sr(p.r || 0) - 3, "text-anchor": "middle", "font-size": 10, fill: "#18181b" });
      t.textContent = p.label; svg.appendChild(t);
    });
  }

  function initScatter() {
    var svgs = document.querySelectorAll("svg[data-scatter]");
    for (var i = 0; i < svgs.length; i++) renderScatter(svgs[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initScatter);
  } else {
    initScatter();
  }
})();
```

- [ ] **Step 3: Create `compare_axis.html`**

```html
{# ramcheck/gui/templates/compare_axis.html #}
{% extends "base.html" %}
{% block title_suffix %} · Vergleich{% endblock %}
{% block body %}
<script defer src="/static/scatter.js"></script>

{% if not detail %}
  <div class="card"><div class="card-title">Vergleich</div>
    <p class="muted">Kein vergleichbares Bundle gefunden (Pack fehlt oder nicht ladbar).</p>
    <a href="/">← Übersicht</a>
  </div>
{% else %}
<div class="card">
  <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:1rem">
    <div>
      <div style="font-size:1.1rem; font-weight:700">↔ Vergleich nach {{ detail.axis_label }}</div>
      <div class="muted text-sm" style="margin-top:0.25rem">
        {{ detail.run_name }}
        {% if detail.pack %}· <strong>{{ detail.pack.title }}</strong>
          <a href="/packs/packs/{{ detail.pack.id }}.yaml" style="margin-left:0.4rem">Kriterien →</a>{% endif %}
      </div>
      {% if detail.axis == "model" %}
        <div class="muted text-xs" style="margin-top:0.25rem">Modelle bei {{ detail.projection_label }}: <strong>{{ detail.projection_label }}: {{ detail.projection }}</strong></div>
      {% endif %}
    </div>
    <div class="text-xs" style="text-align:right">
      {# axis switch (only when the other axis is also comparable) #}
      {% if detail.axis == "variant" and detail.projection_options|length > 1 %}
        <a class="btn btn-secondary" href="/compare/{{ detail.run_name }}?axis=model">nach Modell</a>
      {% elif detail.axis == "model" %}
        <a class="btn btn-secondary" href="/compare/{{ detail.run_name }}?axis=variant">nach Variante</a>
      {% endif %}
      {# projection switch #}
      {% for opt in detail.projection_options %}
        <a class="btn btn-secondary" style="margin-left:0.25rem"
           href="/compare/{{ detail.run_name }}?axis={{ detail.axis }}{% if detail.axis == 'model' %}&variant={{ opt }}{% else %}&model={{ opt }}{% endif %}">{{ detail.projection_label }}: {{ opt }}</a>
      {% endfor %}
    </div>
  </div>
</div>

{% if detail.pack %}{% include "_method_explainer.html" with context %}{% endif %}

{% if detail.single %}
  <div class="card"><p class="muted">Nur ein {{ detail.axis_label }} im Bundle — nichts zu vergleichen.
    {% if detail.axis == "model" %}<a href="/compare/{{ detail.run_name }}?axis=variant">Nach Variante vergleichen?</a>{% endif %}</p></div>
{% else %}

{# ── ① Effizienz-Relation ─────────────────────────────────────────────── #}
<div class="card">
  <div class="card-title">① Effizienz-Relation — Qualität gegen Kosten</div>
  <div style="display:flex; gap:1rem; flex-wrap:wrap; align-items:center">
    <svg data-scatter data-points="{{ detail.scatter_points | tojson }}" width="340" height="220"
         style="background:var(--bg); border-radius:8px"></svg>
    <div style="flex:1; min-width:220px">
      <p class="text-sm">{{ detail.relations_summary }}</p>
      <p class="muted text-xs">Punktgröße = Peak-RAM (System, <code>sys_used_mb</code>).</p>
    </div>
  </div>
</div>

{# ── ② Kopf-an-Kopf ──────────────────────────────────────────────────── #}
<div class="card">
  <div class="card-title">② Kopf-an-Kopf</div>
  <table class="bundle-table">
    <thead><tr><th></th>{% for c in detail.cells %}<th>{{ c.label }}{% if detail.axis == "model" %}<div class="muted text-xs">Variante: {{ c.variant }}</div>{% endif %}</th>{% endfor %}</tr></thead>
    <tbody>
      <tr><td>Urteil</td>{% for c in detail.cells %}<td>
        {% if c.recommendation == "Ja" %}<span class="badge ja">✓ Ja</span>
        {% elif c.recommendation == "Mit Einschränkung" %}<span class="badge einschr">~ Einschr.</span>
        {% elif c.recommendation == "Nein" %}<span class="badge nein">✗ Nein</span>
        {% else %}<span class="muted text-xs">noch nicht bewertet</span>{% endif %}
        {% if c.safety_passed is false %}<span class="badge nein text-xs">K.-o.</span>{% endif %}
      </td>{% endfor %}</tr>
      <tr><td>Qualität</td>{% for c in detail.cells %}<td>{% if c.pct is not none %}{{ "%.0f"|format(c.pct) }} %{% if detail.winners.pct == c.label %} 🏆{% endif %}{% else %}—{% endif %}</td>{% endfor %}</tr>
      {% for d in detail.dimensions %}
      <tr><td class="muted text-xs">{{ d.id }} {{ d.name }} ×{{ d.weight }}</td>{% for c in detail.cells %}<td>
        {% set s = c.dim_scores.get(d.id) %}{{ s if s is not none else "—" }}{% if detail.winners.get(d.id) == c.label %} 🏆{% endif %}
        {% if d.id == detail.pack.ko_rule.dimension and s is not none and s <= detail.pack.ko_rule.threshold %}<span class="badge nein text-xs">K.-o.</span>{% endif %}
      </td>{% endfor %}</tr>
      {% endfor %}
      <tr><td>Decode</td>{% for c in detail.cells %}<td>{% if c.decode_tps is not none %}{{ "%.0f"|format(c.decode_tps) }} tok/s{% if detail.winners.decode == c.label %} 🏆{% endif %}{% else %}—{% endif %}</td>{% endfor %}</tr>
      <tr><td>TTFT P50</td>{% for c in detail.cells %}<td>{% if c.ttft_p50 is not none %}{{ "%.2f"|format(c.ttft_p50) }} s{% if detail.winners.ttft == c.label %} 🏆{% endif %}{% else %}—{% endif %}</td>{% endfor %}</tr>
      <tr><td>e2e Median</td>{% for c in detail.cells %}<td>{% if c.e2e_med is not none %}{{ "%.2f"|format(c.e2e_med) }} s{% if detail.winners.e2e == c.label %} 🏆{% endif %}{% else %}—{% endif %}</td>{% endfor %}</tr>
      <tr><td>Peak-RAM</td>{% for c in detail.cells %}<td>{% if c.peak_ram_mb is not none %}{{ "%.1f"|format(c.peak_ram_mb / 1024) }} GB{% if detail.winners.ram == c.label %} 🏆{% endif %}<div class="muted text-xs">Druck: {{ c.mem_pressure_max }}</div>{% else %}—{% endif %}</td>{% endfor %}</tr>
      <tr><td>CPU Ø/Max</td>{% for c in detail.cells %}<td>{% if c.cpu_max is not none %}{{ "%.0f"|format(c.cpu_avg) }} / {{ "%.0f"|format(c.cpu_max) }} %{% if detail.winners.cpu == c.label %} 🏆{% endif %}{% else %}<span class="muted">n. v.</span>{% endif %}</td>{% endfor %}</tr>
    </tbody>
  </table>
</div>

{# ── ③ Per-Aufgabe-Drill-down (V7) ───────────────────────────────────── #}
{% if detail.divergence %}
<div class="card">
  <div class="card-title">③ Wo gehen sie bei einzelnen Aufgaben auseinander</div>
  <p class="muted text-xs">Per-Prompt-Δ der Judge-Scores (1–5) — eine <em>per-Aufgabe</em>-Sicht, orthogonal zur holistischen Qualitäts-%, nicht deren Herleitung.</p>
  {% for p in detail.divergence %}
  <div x-data="{ open: false }" style="border:1px solid var(--border); border-radius:8px; margin-bottom:0.4rem; overflow:hidden">
    <div class="flex items-center gap-2" style="padding:0.5rem 0.75rem; cursor:pointer; background:var(--bg)" @click="open = !open">
      <span class="prompt-id">{{ p.prompt_id }}</span>
      <span class="text-sm" style="flex:1">{{ p.title }}</span>
      <span class="text-xs muted">Δ {{ "%.1f"|format(p.delta) }}</span>
      {% for c in detail.cells %}<span class="text-xs">{{ c.label }}: {% set sc = p.scores.get(c.label) %}{{ "%.1f"|format(sc) if sc is not none else "—" }}</span>{% endfor %}
      <span class="muted text-xs" x-text="open ? '▲' : '▼'"></span>
    </div>
    <div x-show="open" x-cloak style="padding:0.75rem; display:flex; gap:1rem; flex-wrap:wrap">
      {% for a in p.answers %}
      <div style="flex:1; min-width:220px">
        <div class="muted text-xs" style="text-transform:uppercase; letter-spacing:0.05em">{{ a.label }}{% if a.score is not none %} · {{ a.score }}/5{% endif %}{% if a.red_flag %} <span class="badge nein text-xs">Red-Flag</span>{% endif %}</div>
        <div style="background:var(--card-bg); border-radius:6px; padding:0.5rem 0.7rem; white-space:pre-wrap; font-size:0.82rem; margin-top:0.25rem">{{ a.response_text[:400] }}{% if a.response_text|length > 400 %}…{% endif %}</div>
        {% if a.rationale %}<div class="muted text-xs" style="margin-top:0.25rem">Judge: {{ a.rationale }}</div>{% endif %}
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

{% endif %}{# /single #}

<div class="card"><a href="/result/{{ detail.run_name }}">← Ergebnis-Detail</a> · <a href="/compare">Cross-Run-Aggregat (Station 6)</a></div>
{% endif %}{# /detail #}
{% endblock %}
```

> `{% include "_method_explainer.html" with context %}` passes the current context; the partial reads `pack`. Because the partial uses bare `pack`, set it: change the include to `{% with pack=detail.pack %}{% include "_method_explainer.html" %}{% endwith %}`.

Apply that include form:

```html
{% if detail.pack %}{% with pack=detail.pack %}{% include "_method_explainer.html" %}{% endwith %}{% endif %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_compare_route.py -q`
Expected: PASS (all route tests). Then run the route commit from Task 7 Step 5 if not yet committed.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/templates/compare_axis.html ramcheck/gui/static/scatter.js ramcheck/gui/app.py ramcheck/gui/compare.py tests/test_gui_compare_route.py
git commit -m "feat(compare): compare_axis.html 3-layer view + scatter.js + route wiring"
```

---

## Task 9: „↔ Vergleichen"-Links in `result.html` + `overview.html`

**Files:**
- Modify: `ramcheck/gui/app.py` (pass link data into `/result` and `/`)
- Modify: `ramcheck/gui/templates/result.html`
- Modify: `ramcheck/gui/templates/overview.html`
- Test: `tests/test_gui_compare_route.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gui_compare_route.py
def test_result_shows_compare_link_when_comparable(tmp_path):
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert r.status_code == 200
    assert f"/compare/{d.name}?axis=variant" in r.text
    assert "↔ Vergleichen" in r.text


def test_result_no_compare_link_when_single(tmp_path):
    d = tmp_path / "2026_eval_one"
    _write_compare_bundle(
        d, cells=[("m", "baseline")],
        dim_scores_by_cell={("m", "baseline"): {q: 4 for q in ["Q1","Q2","Q3","Q4","Q5","Q6","Q7"]}},
    )
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert "↔ Vergleichen" not in r.text


def test_overview_shows_compare_link_for_multi_variant(tmp_path):
    d = _two_variant_bundle(tmp_path)
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert f"/compare/{d.name}?axis=variant" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare_route.py -k "compare_link or no_compare" -q`
Expected: FAIL — links not rendered yet.

- [ ] **Step 3a: Pass data from the routes (app.py)**

`/result` route — compute axis options from the already-loaded detail and pass it:

```python
        compare_opts = (
            compare.axis_options(detail["responses"]) if detail and detail.get("responses") else None
        )
        return render(
            "result.html", request, detail=detail, summary=summary, run_dir=rd,
            compare_opts=compare_opts, active="overview",
        )
```

`/` overview route — build a per-bundle map (robust, cheap):

```python
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> HTMLResponse:
        items = bundles.discover(runs_dir)
        compare_links = {
            b.run_dir.name: compare.axis_options_for_dir(b.run_dir) for b in items
        }
        return render(
            "overview.html", request, bundles=items, compare_links=compare_links, active="overview"
        )
```

- [ ] **Step 3b: `result.html` — add the link in the header card actions**

Inside the header `<div style="text-align:right; ...">` block (where the verdict badges render), append after the badges loop:

```html
      {% if compare_opts and compare_opts.comparable %}
        <div style="margin-top:0.4rem">
          <a class="btn btn-secondary" href="/compare/{{ run_dir.name }}?axis={{ compare_opts.default_axis }}">↔ Vergleichen ({{ compare_opts.default_label }})</a>
        </div>
      {% endif %}
```

- [ ] **Step 3c: `overview.html` — add the link in the Aktionen cell**

In the actions `<td>` (where „Ergebnis"/„Fortsetzen"/„Judge" buttons are), append:

```html
          {% set co = compare_links.get(b.run_dir.name) %}
          {% if co and co.comparable %}
          <a href="/compare/{{ b.run_dir.name }}?axis={{ co.default_axis }}" class="btn btn-secondary" style="padding:0.2rem 0.5rem;font-size:0.75rem">↔ Vergleichen</a>
          {% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare_route.py -q`
Expected: PASS (all route tests).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/app.py ramcheck/gui/templates/result.html ramcheck/gui/templates/overview.html tests/test_gui_compare_route.py
git commit -m "feat(compare): '↔ Vergleichen' link on result + overview (only when >1 axis value)"
```

---

## Task 10: AGENTS.md + finale Qualitäts-Gates

**Files:**
- Modify: `AGENTS.md`
- Test: full suite + static gates

- [ ] **Step 1: Update AGENTS.md**

Add to the GUI/routes section a paragraph distinguishing the two compare endpoints:

```markdown
- **Zwei Vergleichs-Ebenen, klar getrennt:** `/compare` (Station 6) ist das **Cross-Run-Aggregat**
  über *alle* Bundles (`aggregate.load_all_scores` → Tabelle Hardware×Qualität). `/compare/{bundle}`
  ist der **Innerhalb-Bundle-Achsen-Vergleich** (Ink. 8): eine kontrollierte Ansicht entlang `model`
  oder `variant` *innerhalb eines* Bundles — Effizienz-Scatter (x=Decode, y=Qualität, r=Peak-RAM
  `sys_used_mb`), Kopf-an-Kopf (`master_rows`⨝`reports`-Join; `master_rows` trägt kein `dim_scores`),
  per-Aufgabe-Drill-down (per-Prompt `Verdict.score`-Δ, V7 — orthogonal zur holistischen %).
  CPU wird GUI-seitig aus `resources.jsonl`-Fenstern berechnet (`compare._cpu_for_window`); da
  `cpu_pct` erst mit Ink. 7 kam und kein Bundle seither neu lief, ist CPU heute überall **„n. v."**
  (ein first-class getesteter Zustand). Pure Logik in `ramcheck/gui/compare.py`.
```

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS — all prior tests (239) + the new compare tests, 0 failures.

- [ ] **Step 3: Static gates**

Run:
```bash
uv run mypy ramcheck
uv run ruff check ramcheck tests
uv run ruff format --check ramcheck tests
```
Expected: clean (no errors). If `ruff format --check` flags files, run `uv run ruff format ramcheck tests` and re-stage.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): /compare/{bundle} (within-bundle axis compare) vs /compare (cross-run)"
```

- [ ] **Step 5: Manual smoke (optional, for Johannes)**

Against the real `ndassist` bundle (gitignored): start the GUI on a fixed port, open `/compare/<bundle>?axis=variant` (baseline vs none) — scatter separates both, Kopf-an-Kopf shows Qualität + Speed + RAM (**CPU = „n. v."**), Drill-down opens diverging answers + Verdict rationales. For `axis=model` + non-empty CPU a fresh 2-model eval is needed.

---

## Self-Review (gegen die Spec)

**Spec coverage:**
- V1 (within-bundle, model|variant) → Task 3/6/7. ✔
- V2 (Qualität in Relation zu Kosten, keine verdichtete Kennzahl) → Scatter + Kosten-Zeilen + `_relations_summary` (Task 4/8). ✔
- V3 (3 Schichten, N-fähig) → compare_axis.html ①②③ (Task 8); Scatter & Tabelle iterieren über N cells. ✔
- V4 (Drill-down nutzt Phase-1-Nachvollziehbarkeit, per-Prompt `Verdict.rationale`; dim_rationales nur wenn vorhanden) → `CellAnswer`/`DivergencePrompt` (Task 5), `dim_rationales` aus reports (Task 3). ✔
- V5 (kein Cross-Bundle) → out of scope, nur `/compare/{bundle}`. ✔
- V6 (CPU GUI-seitig aus `resources.jsonl`-Fenstern, „n. v." first-class) → `_cpu_for_window` (Task 2) + CPU-Spalte (Task 8) + Tests. ✔
- V7 (③ per-Aufgabe, orthogonal zur %) → `_divergence` (Task 5), UI-Text in Task 8. ✔
- §3 Default-Achse (model bei >1 Modell sonst variant) → `axis_options` + `compare_detail` resolve (Task 1/3). ✔
- §4 Qualität via `master_rows`⨝`reports` (master ohne dim_scores), Speed/RAM direkt (Peak-RAM=`sys_used_mb`), `pressure_max`, Projektion baseline/erst-gesehen → Task 3. ✔
- §4 reports.jsonl-lose Bundles → bundle_detail-Fallback `_reports_from_scores` (dim_rationales leer → „Begründung nicht erfasst"); abgedeckt durch un-judged Test (Task 7) + bestehender Fallback. ✔
- §5 Dateien (`compare.py`, Route, `compare_axis.html`, `scatter.js`, result/overview-Link) → Tasks 3–9. ✔
- §6 Navigation/keine Sackgassen → Zurück-Links + Achsen-/Projektions-Switch (Task 8). ✔
- §7 Error-Handling (single, un-judged, CPU leer, keine reports.jsonl, korrupte resources.jsonl) → Tasks 7 + try/except in compare_detail + bundle_detail-Defensive. ✔
- §8 Teststrategie (alle Unit + Integration + 2×2-Fixture + CPU-„n. v.") → Tasks 1–9. ✔

**Placeholder scan:** Jeder Code-Step enthält vollständigen Code; keine TODO/„handle edge cases"-Phrasen. ✔
**Type consistency:** `CompareDetail`/`CompareCell`/`DivergencePrompt`/`CellAnswer`/`AxisOptions` durchgängig gleich benannt; `compare_detail(run_dir, axis=None, *, projection=None)`; `axis_options`/`axis_options_for_dir` konsistent; winners-Keys (`pct,decode,ttft,e2e,ram,cpu,<dim.id>`) Template↔`_winners` identisch. ✔
```
