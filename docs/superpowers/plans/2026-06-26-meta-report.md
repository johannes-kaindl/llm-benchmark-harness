# Meta-Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export a hybrid Markdown Meta-Report (cross-run summary + cell-genau detail, ±judging) plus a leaderboard CSV from the N cells selected on `/compare`.

**Architecture:** Decompose the monolithic `render_report_md` (`touchstone/gui/report_md.py`) into reusable, byte-preserving section helpers (guarded by a golden characterization test). A new pure module `touchstone/gui/meta_report.py` composes those helpers into ONE document: a single meta-frontmatter + a cross-run summary (leaderboard from `aggregate.diff_rows`) + per-bundle cell-filtered detail + shared method/glossary once. Two new GET routes serve the MD (with a `judging` toggle) and the CSV.

**Tech Stack:** Python 3.12, uv, FastAPI, Jinja2, Alpine.js (inline), pytest, mypy (strict), ruff.

## Global Constraints

- Python 3.12, `uv` only. Ruff line-length 100. mypy strict over `touchstone/`.
- German in user-facing copy / report output; code + identifiers English.
- OpenAI-compatible-only stays out of scope here (pure read/render path; no engine code).
- Persisted/exported output is Markdown + CSV only — no HTML/PDF/DOCX artifacts.
- Path confinement: every run name from the client is resolved + checked
  `is_relative_to(runs_dir.resolve())` and `is_dir()` → 404 otherwise (mirror the existing
  `/export-report/{name}` guard at `app.py:251-253`).
- `judging=0` (Bewertungs-Auftrag) must hide quality EVERYWHERE in the MD (summary column,
  detail scorecard, per-answer verdict badges, frontmatter headline) so a re-judging cloud AI is
  unbiased. The CSV is exempt (own download, always carries real data).
- Verification gate before merge: `uv run pytest -q` green · `uv run mypy touchstone/` clean ·
  `uv run ruff check . && uv run ruff format --check .` clean · headless GUI smoke.

---

### Task 1: Golden characterization test for `render_report_md`

Locks the current byte output of `render_report_md` so the Task-2 extraction refactor is provably
behavior-preserving. Self-bootstrapping: first run writes the golden files (on today's code), later
runs compare.

**Files:**
- Create: `tests/test_report_md_golden.py`
- Create (generated, then committed): `tests/golden/report_md_judged.md`, `tests/golden/report_md_blank.md`
- Reference (read for fixture patterns, do not modify): `tests/test_gui_report_md.py:36-122`

**Interfaces:**
- Consumes: `touchstone.gui.report_md.render_report_md(detail, glossary, *, include_judging)`,
  `touchstone.gui.glossary.GLOSSARY`, `touchstone.pack.load_pack`, `touchstone.results.{EvalResponse,ModelReport,Verdict}`.
- Produces: golden files under `tests/golden/` consumed only by this test.

- [ ] **Step 1: Write the golden test**

```python
# tests/test_report_md_golden.py
"""Golden characterization test: pins render_report_md's exact output so the
section-extraction refactor (meta-report sub-project E) is provably byte-preserving.
First run writes the golden + skips; commit it; later runs assert byte-equality."""
from pathlib import Path

import pytest

from touchstone.gui.glossary import GLOSSARY
from touchstone.gui.report_md import render_report_md
from touchstone.pack import load_pack
from touchstone.results import EvalResponse, ModelReport, Verdict

PACK = "packs/ndassist.yaml"
HOST = {"chip": "Apple M5 Pro", "ram_gb": "64.0 GB", "machine": "M5-64GB", "engine": "lm-studio"}
GOLDEN = Path(__file__).parent / "golden"


def _resp(prompt_id, **over):
    base = dict(
        pack_id="ndassist", pack_version=1, machine="t", model="m", quant="q4",
        engine="lm-studio", engine_version="0", variant="baseline", category="A",
        prompt_id=prompt_id, repeat=0, response_text="Eine vollständige Antwort.",
        content_empty=False, ttft_s=0.2, decode_tps=30.0, prefill_tps=90.0, e2e_s=1.5,
        prompt_tokens=100, completion_tokens=50, is_cold_start=False, power_source="ac",
        peak_rss_mb=0.0, sys_used_mb=20000.0, mem_pressure_max="normal", throttled=False,
        ok=True, error="", seed=42, t_start=0.0, t_end=1.5, reasoning_chars=0,
    )
    base.update(over)
    return EvalResponse(**base)


def _detail():
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    report = ModelReport(model="m", variant="baseline",
                         dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={})
    verdict = Verdict(model="m", variant="baseline", prompt_id=first.id, repeat=0,
                      category="A", score=4, red_flag=False, rationale="gut",
                      unscored=False, safety_critical=False)
    return {
        "run_dir": None,
        "manifest": {"host": HOST, "date": "2026-06-24",
                     "judge": {"model": "qwen3-30b", "temperature": 0.0}},
        "pack": pk, "responses": [_resp(first.id)], "verdicts": [verdict],
        "reports": [report],
        "master_rows": [{"model": "m", "variant": "baseline", "pct": 80.0,
                         "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
        "cited_ids": {}, "perf": {},
    }


def _check(name: str, md: str) -> None:
    GOLDEN.mkdir(exist_ok=True)
    p = GOLDEN / f"{name}.md"
    if not p.exists():
        p.write_text(md, encoding="utf-8")
        pytest.skip(f"wrote golden {name} (commit it, then re-run)")
    assert md == p.read_text(encoding="utf-8"), f"render_report_md output drifted vs golden {name}"


def test_golden_judged():
    _check("report_md_judged", render_report_md(_detail(), GLOSSARY, include_judging=True))


def test_golden_blank():
    _check("report_md_blank", render_report_md(_detail(), GLOSSARY, include_judging=False))
```

- [ ] **Step 2: Generate the goldens on current code**

Run: `uv run pytest tests/test_report_md_golden.py -q`
Expected: 2 skipped ("wrote golden …"); `tests/golden/report_md_judged.md` + `report_md_blank.md` now exist.

- [ ] **Step 3: Verify the goldens now pass**

Run: `uv run pytest tests/test_report_md_golden.py -q`
Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_report_md_golden.py tests/golden/report_md_judged.md tests/golden/report_md_blank.md
git commit -m "test(report_md): golden characterization to guard section extraction"
```

---

### Task 2: Extract reusable section helpers in `report_md.py` (byte-preserving)

Mechanical refactor: move the body of each reused section out of `render_report_md` into a
module-level helper returning `list[str]`, and replace the inline block with
`out.extend(helper(...))`. Only the sections the meta-report reuses are extracted; Title/TOC/
Überblick/Hardware stay inline (bundle-report-specific). The golden test from Task 1 proves
byte-equality.

**Files:**
- Modify: `touchstone/gui/report_md.py` (extract from `render_report_md`, lines ~414-638)
- Test: `tests/test_report_md_golden.py` (Task 1 — must stay green), `tests/test_gui_report_md.py` (must stay green)

**Interfaces:**
- Produces (new module-level helpers in `report_md.py`, all pure, return `list[str]` of body lines,
  NO trailing "↑ zum Inhalt" link unless stated):
  - `def _prompt_link(pid: str, title_by: dict[str, str], display: str | None = None) -> str`
    — the closure at `report_md.py:318-319` lifted to module level (`title_by` passed explicitly).
  - `def section_methode(*, pack: Any, include_judging: bool, judge: dict[str, Any], reports: list[Any], title_by: dict[str, str], known_ids: set[str]) -> list[str]`
    — lines `415-449` (the `## Bewertungs-Methode` header through the reasoning-only paragraph).
  - `def section_master_scorecard(*, pack: Any, master_rows: list[dict[str, Any]], reports: list[Any], cited_ids: dict[str, list[str]], cells_by: dict[tuple[str, str], Any], glossary: Mapping[str, Glossary], title_by: dict[str, str], known_ids: set[str]) -> list[str]`
    — lines `477-572` (the `## Master-Scorecard` header through the length-bias loop; the
    `if reports: … else: "_Keine Bewertung vorhanden (eval-only)._"` shape preserved).
  - `def section_dimensionen(pack: Any) -> list[str]` — lines `576-581`.
  - `def section_prompt_varianten(pack: Any, glossary: Mapping[str, Glossary]) -> list[str]` — lines `585-594`.
  - `def section_prompts_antworten(*, pack: Any, responses: list[Any], verdicts: list[Any], glossary: Mapping[str, Glossary], title_by: dict[str, str], top: str) -> list[str]`
    — lines `598-628` (INCLUDES the per-category `top` append at line 628).
  - `def section_glossar(glossary: Mapping[str, Glossary]) -> list[str]` — lines `631-637`.

- [ ] **Step 1: Lift `_prompt_link` to module level**

Add near the other helpers (after `_metric_link`, ~line 107):

```python
def _prompt_link(pid: str, title_by: dict[str, str], display: str | None = None) -> str:
    """An Obsidian wikilink to a prompt's heading, or the bare id when unknown."""
    return _wl(f"{pid} · {title_by[pid]}", display or pid) if pid in title_by else pid
```

Inside `render_report_md`, replace the local `def prompt_link(...)` (lines 318-319) with a thin
closure delegating to it (keeps existing call sites byte-identical):

```python
    def prompt_link(pid: str, display: str | None = None) -> str:
        return _prompt_link(pid, title_by, display)
```

- [ ] **Step 2: Extract the six section helpers**

For each helper above: cut the exact line range out of `render_report_md`, wrap it in the new
`def` with the signature given in **Interfaces**, change the section's `w(<x>)` calls to append to a
local `b: list[str] = []` (`b.append(<x>)`) and `return b`. Internal references to `prompt_link`
become `_prompt_link(pid, title_by)`. `glossary`, `pack`, `ko = pack.ko_rule`, `judge`, `reports`,
`master_rows`, `cited_ids`, `cells_by`, `known_ids` come from parameters. Do NOT include the
trailing `w(top)` (caller keeps it) EXCEPT `section_prompts_antworten`, which keeps its per-category
`top` (passed in).

- [ ] **Step 3: Rewrite `render_report_md` to call the helpers**

Replace the extracted inline blocks with (preserving order + the `w(top)` placement):

```python
    # ── Bewertungs-Methode ──
    out.extend(section_methode(pack=pack, include_judging=include_judging, judge=judge,
                               reports=reports, title_by=title_by, known_ids=known_ids))
    w(top)
    # ── Master-Scorecard (judged runs only) ──
    if include_judging:
        out.extend(section_master_scorecard(pack=pack, master_rows=master_rows, reports=reports,
                                            cited_ids=cited_ids, cells_by=cells_by,
                                            glossary=glossary, title_by=title_by, known_ids=known_ids))
        w(top)
    # ── Dimensionen ──
    out.extend(section_dimensionen(pack))
    w(top)
    # ── Prompt-Varianten ──
    out.extend(section_prompt_varianten(pack, glossary))
    w(top)
    # ── Prompts & Antworten ──
    out.extend(section_prompts_antworten(pack=pack, responses=responses, verdicts=verdicts,
                                         glossary=glossary, title_by=title_by, top=top))
    # ── Metrik-Glossar ──
    out.extend(section_glossar(glossary))
    w(top)
    return "\n".join(out) + "\n"
```

(The `## Überblick & Urteil`, `## Bewertungs-Auftrag` via `_eval_task`, `## Hardware & Konfiguration`,
Title and `## Inhalt` blocks stay exactly as they are inline.)

- [ ] **Step 4: Run the golden + existing report tests**

Run: `uv run pytest tests/test_report_md_golden.py tests/test_gui_report_md.py -q`
Expected: all pass (byte-identical output; no behavior change).

- [ ] **Step 5: Type-check + lint**

Run: `uv run mypy touchstone/gui/report_md.py && uv run ruff check touchstone/gui/report_md.py && uv run ruff format --check touchstone/gui/report_md.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add touchstone/gui/report_md.py
git commit -m "refactor(report_md): extract reusable section helpers (byte-preserving, golden-guarded)"
```

---

### Task 3: `meta_report.select_rows` + `filter_detail_to_cells` (pure)

**Files:**
- Create: `touchstone/gui/meta_report.py`
- Test: `tests/test_gui_meta_report.py`

**Interfaces:**
- Consumes: `touchstone.aggregate.PoolRow`.
- Produces:
  - `def select_rows(pool: list[PoolRow], rows: list[str] | None) -> list[PoolRow]` — filter `pool`
    to the ids in `rows`, in the order they appear in `rows` (mirrors `app.py:324-327`).
  - `def filter_detail_to_cells(detail: dict[str, Any], cells: set[tuple[str, str]]) -> dict[str, Any]`
    — a shallow copy with `responses/verdicts/reports/master_rows/cited_ids` narrowed to the
    `(model, variant)` pairs in `cells`; all other keys unchanged.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gui_meta_report.py
from touchstone.aggregate import PoolRow
from touchstone.gui.meta_report import filter_detail_to_cells, select_rows


def _pr(run_name, model, variant, quality=40.0):
    return PoolRow(id=f"{run_name}|{model}|{variant}", run_name=run_name, chip="M5", ram_gb="64",
                   pack="ndassist", pack_version="1", model=model, quant="q4", variant=variant,
                   quality_pct=quality, ttft_p50="0.40", decode_med="18.2",
                   peak_ram_gb="20.0", model_delta_gb="4.1", power="ac")


def test_select_rows_filters_and_orders():
    pool = [_pr("r1", "a", "baseline"), _pr("r1", "b", "baseline"), _pr("r2", "a", "baseline")]
    out = select_rows(pool, ["r2|a|baseline", "r1|b|baseline"])
    assert [r.id for r in out] == ["r2|a|baseline", "r1|b|baseline"]  # selection order preserved


def test_select_rows_empty_or_none():
    pool = [_pr("r1", "a", "baseline")]
    assert select_rows(pool, None) == []
    assert select_rows(pool, ["", "nope|x|y"]) == []


class _R:
    def __init__(self, model, variant):
        self.model, self.variant = model, variant


def test_filter_detail_to_cells_narrows_only_cell_keyed_lists():
    detail = {
        "pack": "PK", "manifest": {"x": 1}, "perf": {"p": 2}, "run_dir": None,
        "responses": [_R("a", "baseline"), _R("a", "none"), _R("b", "baseline")],
        "verdicts": [_R("a", "baseline"), _R("b", "baseline")],
        "reports": [_R("a", "baseline"), _R("a", "none")],
        "master_rows": [{"model": "a", "variant": "baseline"}, {"model": "b", "variant": "baseline"}],
        "cited_ids": {"a|baseline|d1": ["p1"], "b|baseline|d1": ["p2"], "a|none|d1": ["p3"]},
    }
    out = filter_detail_to_cells(detail, {("a", "baseline")})
    assert [(r.model, r.variant) for r in out["responses"]] == [("a", "baseline")]
    assert [(r.model, r.variant) for r in out["verdicts"]] == [("a", "baseline")]
    assert [(r.model, r.variant) for r in out["reports"]] == [("a", "baseline")]
    assert out["master_rows"] == [{"model": "a", "variant": "baseline"}]
    assert out["cited_ids"] == {"a|baseline|d1": ["p1"]}
    # untouched keys pass through, original not mutated
    assert out["pack"] == "PK" and out["perf"] == {"p": 2}
    assert len(detail["responses"]) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_gui_meta_report.py -q`
Expected: FAIL (`ModuleNotFoundError: touchstone.gui.meta_report`).

- [ ] **Step 3: Implement**

```python
# touchstone/gui/meta_report.py
"""Pure renderers for the cross-run Meta-Report (sub-project E): compose the report_md
section helpers + an aggregate diff into ONE Obsidian Markdown document over N selected
cells, plus a leaderboard CSV. Pure (PoolRows + detail dicts → str), unit-testable.
"""

from __future__ import annotations

from typing import Any

from touchstone.aggregate import PoolRow


def select_rows(pool: list[PoolRow], rows: list[str] | None) -> list[PoolRow]:
    """The PoolRows whose id is in `rows`, in `rows` order (mirrors the /compare selection)."""
    wanted = [x for x in (rows or []) if x]
    by_id = {r.id: r for r in pool}
    return [by_id[i] for i in wanted if i in by_id]


def filter_detail_to_cells(detail: dict[str, Any], cells: set[tuple[str, str]]) -> dict[str, Any]:
    """A shallow copy of `detail` with the cell-keyed lists narrowed to `cells`
    ((model, variant) pairs); pack/manifest/perf/run_dir/etc. pass through unchanged."""
    out = dict(detail)
    out["responses"] = [r for r in detail.get("responses") or [] if (r.model, r.variant) in cells]
    out["verdicts"] = [v for v in detail.get("verdicts") or [] if (v.model, v.variant) in cells]
    out["reports"] = [r for r in detail.get("reports") or [] if (r.model, r.variant) in cells]
    out["master_rows"] = [
        row for row in detail.get("master_rows") or [] if (row["model"], row["variant"]) in cells
    ]
    out["cited_ids"] = {
        k: v
        for k, v in (detail.get("cited_ids") or {}).items()
        if (k.split("|", 2)[0], k.split("|", 2)[1]) in cells
    }
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_gui_meta_report.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/meta_report.py tests/test_gui_meta_report.py
git commit -m "feat(meta_report): select_rows + filter_detail_to_cells (pure)"
```

---

### Task 4: `render_meta_leaderboard_csv` (pure)

**Files:**
- Modify: `touchstone/gui/meta_report.py`
- Test: `tests/test_gui_meta_report.py`

**Interfaces:**
- Produces: `def render_meta_leaderboard_csv(selected: list[PoolRow]) -> str` — header + one row per
  selected cell. Columns (exact order): `run_name, model, variant, pack, pack_version, chip,
  ram_gb, quant, quality_pct, ttft_p50, decode_med, model_delta_gb, peak_ram_gb, power`. Always
  carries real data (no judging toggle); `quality_pct` is the number or "" when None.

- [ ] **Step 1: Write failing test** (append to `tests/test_gui_meta_report.py`)

```python
import csv
import io

from touchstone.gui.meta_report import render_meta_leaderboard_csv


def test_leaderboard_csv_one_row_per_cell():
    out = render_meta_leaderboard_csv([_pr("r1", "a", "baseline", quality=40.0),
                                       _pr("r2", "b", "none", quality=None)])
    reader = list(csv.DictReader(io.StringIO(out)))
    assert reader[0]["run_name"] == "r1" and reader[0]["model"] == "a"
    assert reader[0]["quality_pct"] == "40.0"
    assert reader[1]["quality_pct"] == ""  # None → empty cell
    assert reader[0]["decode_med"] == "18.2" and reader[0]["model_delta_gb"] == "4.1"
    # exact column order
    assert reader[0].keys().__iter__().__next__() == "run_name"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_gui_meta_report.py -k leaderboard_csv -q`
Expected: FAIL (`ImportError: cannot import name 'render_meta_leaderboard_csv'`).

- [ ] **Step 3: Implement** (append to `touchstone/gui/meta_report.py`)

```python
import csv
import io

_CSV_COLUMNS = [
    "run_name", "model", "variant", "pack", "pack_version", "chip", "ram_gb", "quant",
    "quality_pct", "ttft_p50", "decode_med", "model_delta_gb", "peak_ram_gb", "power",
]


def render_meta_leaderboard_csv(selected: list[PoolRow]) -> str:
    """One row per selected cell — the analyst's spreadsheet artifact (always real data)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for r in selected:
        writer.writerow({
            "run_name": r.run_name, "model": r.model, "variant": r.variant, "pack": r.pack,
            "pack_version": r.pack_version, "chip": r.chip, "ram_gb": r.ram_gb, "quant": r.quant,
            "quality_pct": "" if r.quality_pct is None else r.quality_pct,
            "ttft_p50": r.ttft_p50, "decode_med": r.decode_med, "model_delta_gb": r.model_delta_gb,
            "peak_ram_gb": r.peak_ram_gb, "power": r.power,
        })
    return buf.getvalue()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_gui_meta_report.py -k leaderboard_csv -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/meta_report.py tests/test_gui_meta_report.py
git commit -m "feat(meta_report): render_meta_leaderboard_csv (pure)"
```

---

### Task 5: `render_meta_report_md` (pure, composes summary + detail)

**Files:**
- Modify: `touchstone/gui/meta_report.py`
- Test: `tests/test_gui_meta_report.py`

**Interfaces:**
- Consumes: `report_md.section_dimensionen/section_prompt_varianten/section_methode/
  section_master_scorecard/section_prompts_antworten/section_glossar/_eval_task/_load_doc/_wl/_yaml`,
  `aggregate.diff_rows`.
- Produces: `def render_meta_report_md(selected: list[PoolRow], details: list[dict[str, Any]],
  glossary: Mapping[str, Glossary], *, include_judging: bool) -> str`.
  `details` = one cell-filtered `bundle_detail` dict per distinct run_name, in first-appearance
  order of `selected`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_gui_meta_report.py`)

```python
from touchstone.gui.glossary import GLOSSARY
from touchstone.gui.meta_report import render_meta_report_md
from touchstone.pack import load_pack
from touchstone.results import EvalResponse, ModelReport, Verdict

PACK = "packs/ndassist.yaml"
HOST = {"chip": "Apple M5 Pro", "ram_gb": "64.0 GB", "engine": "lm-studio"}


def _resp(pid, model="m", variant="baseline", **over):
    base = dict(pack_id="ndassist", pack_version=1, machine="t", model=model, quant="q4",
                engine="lm-studio", engine_version="0", variant=variant, category="A",
                prompt_id=pid, repeat=0, response_text="Antwort.", content_empty=False,
                ttft_s=0.2, decode_tps=30.0, prefill_tps=90.0, e2e_s=1.5, prompt_tokens=100,
                completion_tokens=50, is_cold_start=False, power_source="ac", peak_rss_mb=0.0,
                sys_used_mb=20000.0, mem_pressure_max="normal", throttled=False, ok=True,
                error="", seed=42, t_start=0.0, t_end=1.5, reasoning_chars=0)
    base.update(over)
    return EvalResponse(**base)


def _detail(model="m", variant="baseline", run_name="r1"):
    from pathlib import Path
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    rep = ModelReport(model=model, variant=variant,
                      dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={})
    ver = Verdict(model=model, variant=variant, prompt_id=first.id, repeat=0, category="A",
                  score=4, red_flag=False, rationale="gut", unscored=False, safety_critical=False)
    return {"run_dir": Path(f"runs/{run_name}"), "manifest": {"host": HOST, "date": "2026-06-24"},
            "pack": pk, "responses": [_resp(first.id, model, variant)], "verdicts": [ver],
            "reports": [rep],
            "master_rows": [{"model": model, "variant": variant, "pct": 80.0,
                             "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
            "cited_ids": {}, "perf": {}}


def test_meta_report_judged_has_summary_and_detail():
    sel = [_pr("r1", "m", "baseline", 80.0), _pr("r1", "m", "none", 40.0)]
    md = render_meta_report_md(sel, [_detail("m", "baseline"), _detail("m", "none")],
                               GLOSSARY, include_judging=True)
    assert md.startswith("---\n") and "type: \"meta_report\"" in md
    assert "## Summary" in md and "## Detail" in md
    assert "Quality" in md.split("## Detail")[0]          # quality column present in summary
    assert "## Bewertungs-Methode" in md and "## Metrik-Glossar" in md
    assert md.count("## Metrik-Glossar") == 1             # glossary exactly once


def test_meta_report_blank_hides_quality_everywhere():
    sel = [_pr("r1", "m", "baseline", 80.0), _pr("r1", "m", "none", 40.0)]
    md = render_meta_report_md(sel, [_detail("m", "baseline"), _detail("m", "none")],
                               GLOSSARY, include_judging=False)
    summary = md.split("## Detail")[0]
    assert "Quality" not in summary                        # quality column dropped
    assert "80" not in summary                             # no leaked score
    assert "## 📋 Bewertungs-Auftrag" in md or "Vorlage:" in md
    assert "## Master-Scorecard" not in md


def test_meta_report_single_cell_no_trophy():
    md = render_meta_report_md([_pr("r1", "m", "baseline", 80.0)], [_detail("m", "baseline")],
                               GLOSSARY, include_judging=True)
    assert "🏆" not in md.split("## Detail")[0]            # one cell → no winners
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_gui_meta_report.py -k meta_report -q`
Expected: FAIL (`ImportError: cannot import name 'render_meta_report_md'`).

- [ ] **Step 3: Implement** (append to `touchstone/gui/meta_report.py`)

```python
from collections.abc import Mapping

from touchstone import aggregate
from touchstone.gui import report_md
from touchstone.gui.glossary import Glossary

_LEADER_METRICS = [  # (winner-key, label, PoolRow attr, is_quality)
    ("quality_pct", "Quality %", "quality_pct", True),
    ("decode_med", "Decode (tok/s)", "decode_med", False),
    ("ttft_p50", "TTFT P50 (s)", "ttft_p50", False),
    ("model_delta_gb", "Modell-Δ (GB)", "model_delta_gb", False),
    ("peak_ram_gb", "System-Peak (GB)", "peak_ram_gb", False),
]


def _num(s: Any) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _meta_frontmatter(selected: list[PoolRow], *, include_judging: bool) -> list[str]:
    fm: list[tuple[str, Any]] = [
        ("title", f"Meta-Report ({len(selected)} Zellen)"),
        ("type", "meta_report"),
        ("date", ""),
        ("n_cells", len(selected)),
        ("judging", "included" if include_judging else "bewertungs-auftrag"),
    ]
    out = ["---"]
    for k, v in fm:
        out.append(f"{k}: {report_md._yaml(v)}")
    for key, vals in (
        ("packs", sorted({r.pack for r in selected})),
        ("models", sorted({r.model for r in selected})),
        ("variants", sorted({r.variant for r in selected})),
        ("runs", sorted({r.run_name for r in selected})),
    ):
        out.append(f"{key}:")
        out.extend(f"  - {report_md._yaml(x)}" for x in vals)
    out.append("---")
    return out


def _cellval(r: PoolRow, attr: str) -> str:
    v = getattr(r, attr)
    if attr == "quality_pct":
        return "—" if v is None else f"{v:.0f}%"
    return str(v) if v not in (None, "") else "—"


def _summary_section(selected: list[PoolRow], *, include_judging: bool) -> list[str]:
    metrics = [m for m in _LEADER_METRICS if include_judging or not m[3]]  # drop quality if blank
    diff = aggregate.diff_rows(selected) if len(selected) >= 2 else None
    winners = diff.winners if diff else {}
    out = ["## Summary\n"]
    if diff and diff.common:
        out.append("**Gemeinsam:** " + " · ".join(f"{k}={v}" for k, v in diff.common if v) + "\n")
    out.append("| Modell·Variante | " + " | ".join(m[1] for m in metrics) + " |")
    out.append("|---|" + "|".join("---" for _ in metrics) + "|")
    if include_judging:
        rows = sorted(selected, key=lambda r: (r.quality_pct is not None, r.quality_pct or 0.0),
                      reverse=True)
    else:
        rows = sorted(selected, key=lambda r: (_num(r.decode_med) is not None, _num(r.decode_med) or 0.0),
                      reverse=True)
    for r in rows:
        cells = []
        for key, _label, attr, _q in metrics:
            val = _cellval(r, attr)
            if winners.get(key) == r.id and val != "—":
                val += " 🏆"
            cells.append(val)
        out.append(f"| {r.model}·{r.variant} | " + " | ".join(cells) + " |")
    out.append("")
    return out


def _bundle_detail_section(
    detail: dict[str, Any], glossary: Mapping[str, Glossary], *, include_judging: bool, top: str
) -> list[str]:
    pack = detail["pack"]
    run_dir = detail.get("run_dir")
    run_name = run_dir.name if run_dir is not None else "bundle"
    manifest = detail.get("manifest") or {}
    host = manifest.get("host") or {}
    responses = detail.get("responses") or []
    verdicts = detail.get("verdicts") or []
    reports = detail.get("reports") or []
    master_rows = detail.get("master_rows") or []
    cited_ids = detail.get("cited_ids") or {}
    title_by = {p.id: p.title for _, p in pack.all_prompts()}
    known_ids = {p.id for _, p in pack.all_prompts()}
    chip = host.get("chip", "")
    out = [f"### Bundle {run_name} · {pack.title}" + (f" · {chip}" if chip else "") + "\n"]
    if include_judging:
        doc = report_md._load_doc(run_dir, pack, responses, verdicts, reports, host, manifest)
        cells_by = {(c.model, c.variant): c for c in doc.cells} if doc is not None else {}
        out.extend(report_md.section_master_scorecard(
            pack=pack, master_rows=master_rows, reports=reports, cited_ids=cited_ids,
            cells_by=cells_by, glossary=glossary, title_by=title_by, known_ids=known_ids))
    else:
        out.append(report_md._eval_task(
            pack, responses, lambda pid, d=None: report_md._prompt_link(pid, title_by, d), known_ids))
    out.extend(report_md.section_prompts_antworten(
        pack=pack, responses=responses, verdicts=verdicts, glossary=glossary,
        title_by=title_by, top=top))
    return out


def render_meta_report_md(
    selected: list[PoolRow], details: list[dict[str, Any]],
    glossary: Mapping[str, Glossary], *, include_judging: bool,
) -> str:
    out = _meta_frontmatter(selected, include_judging=include_judging)
    w = out.append
    packs = sorted({r.pack for r in selected})
    w(f"\n# Meta-Report — {', '.join(packs) or '—'} · {len(selected)} Zellen\n")
    w("## Inhalt\n")
    for label in ["Summary", "Detail", "Bewertungs-Methode", "Metrik-Glossar"]:
        w(f"- {report_md._wl(label)}")
    w("")
    top = f"\n{report_md._wl('Inhalt', '↑ zum Inhalt')}\n"

    out.extend(_summary_section(selected, include_judging=include_judging))
    w(top)

    w("## Detail\n")
    # shared pack sections once per distinct pack (dimensions + prompt variants)
    seen: dict[str, Any] = {}
    for d in details:
        pk = d["pack"]
        seen.setdefault(pk.id, pk)
    for pk in seen.values():
        out.extend(report_md.section_dimensionen(pk))
        out.extend(report_md.section_prompt_varianten(pk, glossary))
    for d in details:
        out.extend(_bundle_detail_section(d, glossary, include_judging=include_judging, top=top))

    # Bewertungs-Methode once per distinct pack (static + that pack's K.-o./scale; no judge identity)
    for pk in seen.values():
        title_by = {p.id: p.title for _, p in pk.all_prompts()}
        known_ids = {p.id for _, p in pk.all_prompts()}
        out.extend(report_md.section_methode(
            pack=pk, include_judging=include_judging, judge={}, reports=[],
            title_by=title_by, known_ids=known_ids))
    w(top)
    out.extend(report_md.section_glossar(glossary))
    w(top)
    return "\n".join(out) + "\n"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_gui_meta_report.py -k meta_report -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Type-check + lint + full meta test file**

Run: `uv run pytest tests/test_gui_meta_report.py -q && uv run mypy touchstone/gui/meta_report.py && uv run ruff check touchstone/gui/meta_report.py && uv run ruff format --check touchstone/gui/meta_report.py`
Expected: all green/clean.

- [ ] **Step 6: Commit**

```bash
git add touchstone/gui/meta_report.py tests/test_gui_meta_report.py
git commit -m "feat(meta_report): render_meta_report_md — summary + cell-genau detail (±judging)"
```

---

### Task 6: Route `GET /export-meta-csv`

**Files:**
- Modify: `touchstone/gui/app.py` (add route next to `export_report`, ~after line 264)
- Test: `tests/test_gui_meta_report_routes.py`

**Interfaces:**
- Consumes: `aggregate_mod.pool_rows(runs_dir)`, `meta_report.select_rows`,
  `meta_report.render_meta_leaderboard_csv`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_gui_meta_report_routes.py
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.pack import load_pack

PACK = "packs/ndassist.yaml"
HOST = {"chip": "Apple M5 Pro", "ram_gb": "64.0 GB", "machine": "M5-64GB", "engine": "lm-studio"}


class _FakeLauncher:
    def spawn(self, argv): return 1
    def alive(self, pid): return False
    def terminate(self, pid): return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _write_bundle(d, *, model="m", variant="baseline"):
    pk = load_pack(PACK)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.json").write_text(json.dumps(
        {"pack_id": pk.id, "pack_path": PACK, "models": [{"id": model, "quant": "q4"}],
         "host": HOST, "date": "2026-06-24", "seed": 42}), encoding="utf-8")
    first = next(p for _, p in pk.all_prompts())
    from touchstone.gui.report_md import _frontmatter  # noqa: F401  (ensure import path valid)
    base = dict(pack_id=pk.id, pack_version=1, machine="t", model=model, quant="q4",
                engine="lm-studio", engine_version="0", variant=variant, category="A",
                prompt_id=first.id, repeat=0, response_text="Antwort.", content_empty=False,
                ttft_s=0.2, decode_tps=30.0, prefill_tps=90.0, e2e_s=1.5, prompt_tokens=100,
                completion_tokens=50, is_cold_start=False, power_source="ac", peak_rss_mb=0.0,
                sys_used_mb=20000.0, mem_pressure_max="normal", throttled=False, ok=True,
                error="", seed=42, t_start=0.0, t_end=1.5, reasoning_chars=0)
    (d / "responses.jsonl").write_text(json.dumps(base) + "\n", encoding="utf-8")
    header = ["model", "variant", "metric_type", "metric", "weight", "score",
              "chip", "ram_gb", "pack", "pack_version", "quant",
              "ttft_p50", "decode_med", "peak_ram_gb", "model_delta_gb", "power"]
    lines = [",".join(header)]
    for dim in pk.dimensions:
        lines.append(f"{model},{variant},dimension,{dim.id},{dim.weight},4,"
                     f"M5,64,ndassist,1,q4,0.40,18.2,20.0,4.1,ac")
    (d / "scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"{d.name}|{model}|{variant}"


def test_export_meta_csv(tmp_path):
    id1 = _write_bundle(tmp_path / "2026_eval_a", model="a")
    id2 = _write_bundle(tmp_path / "2026_eval_b", model="b")
    r = _client(tmp_path).get(f"/export-meta-csv?rows={id1}&rows={id2}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "meta-leaderboard-2-zellen.csv" in r.headers.get("content-disposition", "")
    body = r.text.splitlines()
    assert body[0].startswith("run_name,model,variant")
    assert len(body) == 3  # header + 2 rows


def test_export_meta_csv_empty_selection_400(tmp_path):
    assert _client(tmp_path).get("/export-meta-csv").status_code == 400
    assert _client(tmp_path).get("/export-meta-csv?rows=nope|x|y").status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_gui_meta_report_routes.py -k meta_csv -q`
Expected: FAIL (404 — route not defined yet).

- [ ] **Step 3: Implement** (insert after the `export_report` route, ~line 264 in `app.py`)

```python
    @app.get("/export-meta-csv")
    def export_meta_csv(rows: list[str] | None = Query(default=None)) -> Any:
        """Leaderboard CSV (one row per selected /compare cell) — the analyst's data artifact."""
        from touchstone.gui import meta_report

        selected = meta_report.select_rows(aggregate_mod.pool_rows(runs_dir), rows)
        if not selected:
            raise HTTPException(status_code=400, detail="keine Auswahl")
        body = meta_report.render_meta_leaderboard_csv(selected)
        fname = f"touchstone-meta-leaderboard-{len(selected)}-zellen.csv"
        return Response(
            body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_gui_meta_report_routes.py -k meta_csv -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/app.py tests/test_gui_meta_report_routes.py
git commit -m "feat(gui): GET /export-meta-csv — leaderboard CSV over selected cells"
```

---

### Task 7: Route `GET /export-meta-report`

**Files:**
- Modify: `touchstone/gui/app.py` (add route after `export_meta_csv`)
- Test: `tests/test_gui_meta_report_routes.py`

**Interfaces:**
- Consumes: `aggregate_mod.pool_rows`, `meta_report.select_rows`, `bundles.bundle_detail`,
  `meta_report.filter_detail_to_cells`, `meta_report.render_meta_report_md`,
  `touchstone.gui.glossary.GLOSSARY`. Path-guard each run via the `runs_dir` resolve/`is_dir` check.

- [ ] **Step 1: Write failing tests** (append to `tests/test_gui_meta_report_routes.py`)

```python
def test_export_meta_report_md(tmp_path):
    id1 = _write_bundle(tmp_path / "2026_eval_a", model="a")
    id2 = _write_bundle(tmp_path / "2026_eval_b", model="b")
    r = _client(tmp_path).get(f"/export-meta-report?rows={id1}&rows={id2}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "meta-report-2-zellen.md" in r.headers.get("content-disposition", "")
    assert "## Summary" in r.text and "## Detail" in r.text


def test_export_meta_report_blank_suffix_and_no_quality(tmp_path):
    id1 = _write_bundle(tmp_path / "2026_eval_a", model="a")
    r = _client(tmp_path).get(f"/export-meta-report?rows={id1}&judging=0")
    assert r.status_code == 200
    assert "zum-bewerten.md" in r.headers.get("content-disposition", "")
    assert "Quality" not in r.text.split("## Detail")[0]
    assert "## Master-Scorecard" not in r.text


def test_export_meta_report_empty_400(tmp_path):
    assert _client(tmp_path).get("/export-meta-report").status_code == 400


def test_export_meta_report_traversal_rejected(tmp_path):
    # a row id whose run_name escapes runs_dir must not read outside it
    r = _client(tmp_path).get("/export-meta-report?rows=../etc|m|baseline")
    assert r.status_code in (400, 404)  # filtered out (no such pool row) → 400, or guarded → 404
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_gui_meta_report_routes.py -k meta_report -q`
Expected: FAIL (404 — route not defined).

- [ ] **Step 3: Implement** (insert after `export_meta_csv` in `app.py`)

```python
    @app.get("/export-meta-report")
    def export_meta_report(rows: list[str] | None = Query(default=None), judging: int = 1) -> Any:
        """Hybrid cross-run Meta-Report (summary + cell-genau detail) over the selected /compare
        cells. judging=0 strips all quality → a fresh Bewertungs-Auftrag for a cloud AI."""
        from touchstone.gui import meta_report
        from touchstone.gui.glossary import GLOSSARY

        selected = meta_report.select_rows(aggregate_mod.pool_rows(runs_dir), rows)
        if not selected:
            raise HTTPException(status_code=400, detail="keine Auswahl")
        # group selected cells by run_name, in first-appearance order
        order: list[str] = []
        cells_by_run: dict[str, set[tuple[str, str]]] = {}
        for r in selected:
            if r.run_name not in cells_by_run:
                cells_by_run[r.run_name] = set()
                order.append(r.run_name)
            cells_by_run[r.run_name].add((r.model, r.variant))
        details = []
        for run_name in order:
            rd = (runs_dir / run_name).resolve()
            if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
                raise HTTPException(status_code=404)
            detail = bundles.bundle_detail(rd)
            if detail is None:
                continue  # corrupt bundle → skip its cells, never 500
            details.append(meta_report.filter_detail_to_cells(detail, cells_by_run[run_name]))
        include = bool(judging)
        md = meta_report.render_meta_report_md(selected, details, GLOSSARY, include_judging=include)
        suffix = "" if include else "-zum-bewerten"
        fname = f"touchstone-meta-report-{len(selected)}-zellen{suffix}.md"
        return Response(
            md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_gui_meta_report_routes.py -q`
Expected: PASS (all route tests).

- [ ] **Step 5: Type-check + lint**

Run: `uv run mypy touchstone/gui/app.py && uv run ruff check touchstone/gui/app.py && uv run ruff format --check touchstone/gui/app.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add touchstone/gui/app.py tests/test_gui_meta_report_routes.py
git commit -m "feat(gui): GET /export-meta-report — hybrid ±judging meta-report over selected cells"
```

---

### Task 8: `/compare` UI — footer toggle + two export buttons

**Files:**
- Modify: `touchstone/gui/templates/compare.html` (x-data at line 10; sticky footer lines 98-107)
- Test: `tests/test_gui_compare_route.py` (append an assertion-only render test)

**Interfaces:**
- Consumes: existing Alpine `sel` array + the `sel.map(id => 'rows=' + encodeURIComponent(id)).join('&')`
  URL pattern. Adds a `judging` boolean to x-data.

- [ ] **Step 1: Write failing test** (append to `tests/test_gui_compare_route.py`)

```python
def test_compare_page_has_meta_export_controls(tmp_path):
    # reuse this file's existing _client / bundle-writing helpers
    body = _client(tmp_path).get("/compare").text
    assert "/export-meta-report?" in body
    assert "/export-meta-csv?" in body
    assert "judging" in body  # the "mit Bewertung" toggle is wired
```

(If `tests/test_gui_compare_route.py` lacks a `_client` helper, mirror the `_client(tmp_path)` from
`tests/test_gui_report_md.py:31-33`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_gui_compare_route.py -k meta_export_controls -q`
Expected: FAIL (controls absent).

- [ ] **Step 3: Edit the template**

Change the x-data on line 10 to add `judging: true`:

```html
<div class="card" x-data="{ sel: [], chip: '', pack: '', model: '', judging: true }">
```

Replace the sticky footer block (lines 98-107) with (keeps the existing "Vergleichen" button,
adds the toggle + two export buttons; exports enabled at ≥1 selected):

```html
  {# Sticky action footer when ≥1 row selected #}
  <div x-show="sel.length > 0" style="margin-top:1rem; padding:0.5rem 0; border-top:1px solid var(--border,#444); display:flex; align-items:center; gap:1rem; flex-wrap:wrap">
    <span class="muted text-sm"><span x-text="sel.length"></span> Lauf/Läufe ausgewählt</span>
    <button style="padding:0.3rem 0.8rem; font-size:0.85rem; border-radius:4px; border:none; background:var(--accent,#4f8); color:#000; cursor:pointer"
            :disabled="sel.length < 2"
            :style="sel.length < 2 ? 'opacity:0.45;cursor:not-allowed' : ''"
            @click="window.location = '/compare?' + sel.map(id => 'rows=' + encodeURIComponent(id)).join('&')">
      Vergleichen (<span x-text="sel.length"></span>)
    </button>
    <label style="display:flex; align-items:center; gap:0.35rem; font-size:0.85rem">
      <input type="checkbox" x-model="judging"> mit Bewertung
    </label>
    <button style="padding:0.3rem 0.8rem; font-size:0.85rem; border-radius:4px; border:1px solid var(--accent,#4f8); background:transparent; color:inherit; cursor:pointer"
            @click="window.location = '/export-meta-report?' + sel.map(id => 'rows=' + encodeURIComponent(id)).join('&') + '&judging=' + (judging ? 1 : 0)">
      Meta-Report .md
    </button>
    <button style="padding:0.3rem 0.8rem; font-size:0.85rem; border-radius:4px; border:1px solid var(--border,#666); background:transparent; color:inherit; cursor:pointer"
            @click="window.location = '/export-meta-csv?' + sel.map(id => 'rows=' + encodeURIComponent(id)).join('&')">
      Leaderboard .csv
    </button>
  </div>
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_gui_compare_route.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/compare.html tests/test_gui_compare_route.py
git commit -m "feat(gui): /compare footer — meta-report + leaderboard export buttons (±judging toggle)"
```

---

### Task 9: Full verification + headless smoke

**Files:** none (verification only).

- [ ] **Step 1: Full suite + type-check + lint**

Run: `uv run pytest -q && uv run mypy touchstone/ && uv run ruff check . && uv run ruff format --check .`
Expected: all green/clean. (Baseline was 559 tests; expect 559 + new meta tests.)

- [ ] **Step 2: Headless GUI smoke** (Memory `gui-restart-after-changes` — a fresh server)

```bash
# fresh server on a throwaway runs dir with two judged bundles already created by the tests is
# overkill; instead drive the routes against a tmp runs/ via the TestClient smoke below.
uv run python - <<'PY'
from pathlib import Path
import json, tempfile
from fastapi.testclient import TestClient
from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.pack import load_pack

PACK="packs/ndassist.yaml"; HOST={"chip":"M5","ram_gb":"64.0 GB","engine":"lm-studio"}
class L:
    def spawn(s,a): return 1
    def alive(s,p): return False
    def terminate(s,p): return None
def write(d, model):
    pk=load_pack(PACK); d.mkdir(parents=True)
    (d/"bundle.json").write_text(json.dumps({"pack_id":pk.id,"pack_path":PACK,
        "models":[{"id":model,"quant":"q4"}],"host":HOST,"date":"2026-06-24","seed":42}))
    first=next(p for _,p in pk.all_prompts())
    base=dict(pack_id=pk.id,pack_version=1,machine="t",model=model,quant="q4",engine="lm-studio",
        engine_version="0",variant="baseline",category="A",prompt_id=first.id,repeat=0,
        response_text="A.",content_empty=False,ttft_s=0.2,decode_tps=30.0,prefill_tps=90.0,
        e2e_s=1.5,prompt_tokens=100,completion_tokens=50,is_cold_start=False,power_source="ac",
        peak_rss_mb=0.0,sys_used_mb=20000.0,mem_pressure_max="normal",throttled=False,ok=True,
        error="",seed=42,t_start=0.0,t_end=1.5,reasoning_chars=0)
    (d/"responses.jsonl").write_text(json.dumps(base)+"\n")
    h=["model","variant","metric_type","metric","weight","score","chip","ram_gb","pack",
       "pack_version","quant","ttft_p50","decode_med","peak_ram_gb","model_delta_gb","power"]
    rows=[",".join(h)]+[f"{model},baseline,dimension,{dim.id},{dim.weight},4,M5,64,ndassist,1,q4,0.40,18.2,20.0,4.1,ac" for dim in pk.dimensions]
    (d/"scores.csv").write_text("\n".join(rows)+"\n")
    return f"{d.name}|{model}|baseline"
td=Path(tempfile.mkdtemp())
i1=write(td/"2026_eval_a","a"); i2=write(td/"2026_eval_b","b")
c=TestClient(gui_app.create_app(runs_dir=td, registry=RunRegistry(runs_dir=td, launcher=L())))
md=c.get(f"/export-meta-report?rows={i1}&rows={i2}")
blank=c.get(f"/export-meta-report?rows={i1}&judging=0")
csv=c.get(f"/export-meta-csv?rows={i1}&rows={i2}")
assert md.status_code==200 and "## Summary" in md.text and "## Detail" in md.text, "MD failed"
assert "Quality" not in blank.text.split("## Detail")[0], "blank leaked quality"
assert csv.status_code==200 and len(csv.text.splitlines())==3, "CSV failed"
print("SMOKE OK · md", len(md.text), "bytes · csv rows", len(csv.text.splitlines()))
PY
```
Expected: `SMOKE OK · …`.

- [ ] **Step 3: Final commit (if any verification fixups were needed)**

```bash
git add -A && git commit -m "test(meta-report): full-suite + headless smoke green" || echo "nothing to commit"
```

---

## Post-plan (controller, not a subagent task)

- **Adversariale Whole-Branch-Review** (Memory `subagent-workflow-verification`): 3-Linsen-Review
  des gesamten Branch-Diffs vor Merge — Fokus (1) Quality-Leakage bei `judging=0` (Summary/Detail/
  Frontmatter), (2) Pfad-Confinement der zwei Routen, (3) Bytegleichheit der `report_md`-Zerlegung
  (Golden grün). Controller verifiziert jeden Fund selbst, bevor gefixt wird.
- **Merge:** `feat/gui-meta-report` → `main`, Push (Codeberg→GitHub-Mirror). Kein PR (Solo-Repo).

## Self-Review (done by plan author)

- **Spec coverage:** Hybrid (T5) · zell-genau Detail (T3 filter + T5) · ±judging global + judging=0
  hides quality (T5 `_summary_section`/`_bundle_detail_section`, T7 route) · 2 Downloads MD+CSV
  (T6/T7) · `_confine`-Guard (T7) · leere Auswahl 400 (T6/T7) · UI Footer (T8) · report_md
  decomposition byte-preserving (T1/T2) · Tests + headless smoke (T9). All sections mapped.
- **Placeholders:** none — every code step carries complete code or an exact move-instruction with
  line ranges; the extraction (T2) is guarded by the T1 golden.
- **Type consistency:** `select_rows`, `filter_detail_to_cells`, `render_meta_leaderboard_csv`,
  `render_meta_report_md`, the `section_*` helpers and route names are used identically across
  tasks. `winners` keys (`quality_pct/decode_med/ttft_p50/peak_ram_gb/model_delta_gb`) match
  `aggregate.diff_rows`; `CompareColumn.id == PoolRow.id` used for trophy placement.
