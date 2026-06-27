# Cross-Judge-Aggregat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dedicated `/compare-judges` view that aggregates per-bundle `judge_quality.md` results across judge models, grouped by pack, with per-metric winner highlighting — answering "which model is the better judge".

**Architecture:** Enrich F's `judge_quality.md` YAML frontmatter (backward-compatibly) with `pack` + the 3 missing rubric rates, then a new pure module `touchstone/gui/judge_compare.py` discovers + parses every `judge_quality.md` under `runs_dir`, groups rows by pack, and computes per-metric winners (mirroring `aggregate._winner`). A thin GET route renders a grouped table.

**Tech Stack:** Python 3.12 · FastAPI · Jinja2 · PyYAML · pytest · mypy strict · ruff.

## Global Constraints

- **Never crash on file content.** `parse_frontmatter` and `judge_quality_rows` must tolerate arbitrary `.md` content / malformed YAML → return `None` / skip the row, never raise. The route never 500s.
- **Frontmatter enrichment is additive + backward-compatible.** Only NEW frontmatter lines are added to `render_judge_quality_md`; the existing `render_judge_quality_md` BODY (Headline + 3 sections) stays byte-identical. Old `judge_quality.md` files lacking the new fields parse to `None` for those fields → rendered as "—".
- **`detail.get("pack")` is defensive.** `render_judge_quality_md` must read the pack via `detail.get("pack")` (NOT `detail["pack"]`) — an existing unit test calls it with a detail dict that has no `pack` key; missing pack → frontmatter `pack: —`.
- **Comparability = group by pack.** Winners are computed ONLY within a pack group. `mean_abs_delta` lower = better; the 4 rubric rates higher = better. Tie → no winner (`None`), mirroring `aggregate._winner` (`leaders[0] if len(leaders) == 1 else None`).
- **Routing:** the route is `/compare-judges` (flat) — NOT `/compare/judges` (which the existing `/compare/{name}` route at `app.py:385` would shadow as `name="judges"`).
- **Rates are fractions 0..1** in the frontmatter (like the existing `names_improvement_rate = round(n/t, 2)`); the template renders them `× 100` as `%`.
- **Skip trashed bundles** (`.trash` in the path) — a deleted bundle must not appear in the comparison.
- **Gate (every task keeps these green):** `uv run pytest -q` · `uv run mypy touchstone/` · `uv run ruff check . && uv run ruff format --check .`.

## File Structure

- **Modify** `touchstone/gui/judge_meta.py` — enrich the frontmatter block in `render_judge_quality_md` (~lines 243–251): add `pack` + 3 rubric-rate lines. Body untouched.
- **Create** `touchstone/gui/judge_compare.py` — pure: `parse_frontmatter`, `JudgeQualityRow`, `judge_quality_rows`, `PackGroup`, `group_by_pack` (+ `_winner`).
- **Modify** `touchstone/gui/app.py` — `GET /compare-judges` inside `create_app()`.
- **Create** `touchstone/gui/templates/compare_judges.html` — grouped table.
- **Modify** `touchstone/gui/templates/compare.html` — a link to `/compare-judges` in the page header.
- **Modify** `tests/test_judge_meta.py` — `_Pack` gains `.id`; a new test for the enriched frontmatter.
- **Create** `tests/test_judge_compare.py` — pure-logic tests.
- **Create** `tests/test_gui_compare_judges.py` — route test.

---

### Task 1: Enrich F's judge_quality.md frontmatter

**Files:**
- Modify: `touchstone/gui/judge_meta.py` (the frontmatter block inside `render_judge_quality_md`, ~lines 243–251)
- Test: `tests/test_judge_meta.py` (add `.id` to `_Pack`; new test)

**Interfaces:**
- Consumes: `RubricSummary` (`.cites_evidence`, `.justifies_level`, `.catches_safety`, each `tuple[int,int]`); `detail.get("pack")` (a Pack with `.id`, or absent).
- Produces: `render_judge_quality_md` output whose frontmatter now also contains `pack:`, `cites_evidence_rate:`, `justifies_level_rate:`, `catches_safety_rate:` (each a fraction `round(n/t, 2)` or `null`).

- [ ] **Step 1: Write the failing test**

In `tests/test_judge_meta.py`, first give `_Pack` an `id` — change its `__init__` (currently around line 86–89) so the first line of the body sets `self.id`:

```python
class _Pack:
    def __init__(self):
        self.id = "ndassist"
        self.dimensions = [_Dim("Q1", 1), _Dim("Q2", 1)]
        self.ko_rule = _Ko()
```

Then add this new test at the end of the file:

```python
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
    detail = {"run_dir": Path("runs/2026_eval_x"), "manifest": {"judge": {"model": "j"}}, "pack": pk}
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_judge_meta.py -q -k frontmatter_enriched or test_render_judge_quality_md_tolerates_missing_pack`
Expected: FAIL — the new frontmatter lines aren't emitted yet (`pack:` / `*_rate:` assertions fail).

- [ ] **Step 3: Enrich the frontmatter block**

In `touchstone/gui/judge_meta.py`, inside `render_judge_quality_md`, replace the frontmatter-building block (currently):

```python
    bmad = agreement.bundle_mean_abs_delta
    ni_n, ni_t = rubric.names_improvement
    w("---")
    w('type: "judge_quality"')
    w(f"bundle: {bundle}")
    w(f'judge_model: "{judge_model}"')
    w(f"mean_abs_delta: {bmad if bmad is not None else 'null'}")
    w(f"names_improvement_rate: {round(ni_n / ni_t, 2) if ni_t else 'null'}")
    w("---\n")
```

with:

```python
    bmad = agreement.bundle_mean_abs_delta
    ni_n, ni_t = rubric.names_improvement
    ce_n, ce_t = rubric.cites_evidence
    jl_n, jl_t = rubric.justifies_level
    cs_n, cs_t = rubric.catches_safety
    pack_obj = detail.get("pack")
    pack_id = pack_obj.id if pack_obj is not None else "—"
    w("---")
    w('type: "judge_quality"')
    w(f"bundle: {bundle}")
    w(f"pack: {pack_id}")
    w(f'judge_model: "{judge_model}"')
    w(f"mean_abs_delta: {bmad if bmad is not None else 'null'}")
    w(f"names_improvement_rate: {round(ni_n / ni_t, 2) if ni_t else 'null'}")
    w(f"cites_evidence_rate: {round(ce_n / ce_t, 2) if ce_t else 'null'}")
    w(f"justifies_level_rate: {round(jl_n / jl_t, 2) if jl_t else 'null'}")
    w(f"catches_safety_rate: {round(cs_n / cs_t, 2) if cs_t else 'null'}")
    w("---\n")
```

(Only frontmatter lines added; the body below this block is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_judge_meta.py -q`
Expected: PASS — the new tests plus all pre-existing judge_meta tests (including `test_render_judge_quality_md_has_all_sections`, which passes `pack: —` since its detail has no pack key and asserts nothing about pack).

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/judge_meta.py tests/test_judge_meta.py
git commit -m "feat(judge_meta): enrich judge_quality.md frontmatter (pack + 3 rubric rates)"
```

---

### Task 2: Pure frontmatter parser + row discovery

**Files:**
- Create: `touchstone/gui/judge_compare.py`
- Test: `tests/test_judge_compare.py` (create)

**Interfaces:**
- Consumes: `judge_quality.md` files under `runs_dir` with the frontmatter from Task 1.
- Produces: `parse_frontmatter(text: str) -> dict[str, Any] | None`; `JudgeQualityRow` dataclass (fields: `bundle, pack, judge_model: str`; `mean_abs_delta, names_improvement_rate, cites_evidence_rate, justifies_level_rate, catches_safety_rate: float | None`; `key` property = `f"{bundle}|{judge_model}"`); `judge_quality_rows(runs_dir: Path) -> list[JudgeQualityRow]`; module constant `PACK_UNKNOWN: str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_judge_compare.py`:

```python
from pathlib import Path

from touchstone.gui.judge_compare import (
    JudgeQualityRow,
    PACK_UNKNOWN,
    judge_quality_rows,
    parse_frontmatter,
)

FM = (
    '---\ntype: "judge_quality"\nbundle: b1\npack: ndassist\n'
    'judge_model: "qwen3-27b"\nmean_abs_delta: 0.5\nnames_improvement_rate: 0.75\n'
    "cites_evidence_rate: 0.5\njustifies_level_rate: 1.0\ncatches_safety_rate: 0.25\n---\n# body\n"
)


def test_parse_frontmatter_valid():
    fm = parse_frontmatter(FM)
    assert fm is not None
    assert fm["type"] == "judge_quality" and fm["pack"] == "ndassist"
    assert fm["mean_abs_delta"] == 0.5


def test_parse_frontmatter_no_block():
    assert parse_frontmatter("# just a heading\nno frontmatter here\n") is None


def test_parse_frontmatter_malformed_yaml():
    # unterminated/garbage YAML inside a --- block → None, never raises
    assert parse_frontmatter("---\n: : : not yaml : :\n[unclosed\n---\n") is None


def test_parse_frontmatter_non_mapping():
    assert parse_frontmatter("---\n- a\n- b\n---\n") is None


def _write_jq(d: Path, text: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "judge_quality.md").write_text(text, encoding="utf-8")


def test_judge_quality_rows_discovers_and_parses(tmp_path):
    _write_jq(tmp_path / "b1", FM)
    _write_jq(
        tmp_path / "b2",
        '---\ntype: "judge_quality"\nbundle: b2\npack: buero\n'
        'judge_model: "gpt-x"\nmean_abs_delta: 1.2\nnames_improvement_rate: 0.3\n'
        "cites_evidence_rate: 0.4\njustifies_level_rate: 0.6\ncatches_safety_rate: 0.9\n---\n",
    )
    rows = judge_quality_rows(tmp_path)
    assert len(rows) == 2
    by = {r.bundle: r for r in rows}
    assert by["b1"].pack == "ndassist" and by["b1"].judge_model == "qwen3-27b"
    assert by["b1"].mean_abs_delta == 0.5 and by["b1"].cites_evidence_rate == 0.5
    assert by["b1"].key == "b1|qwen3-27b"


def test_judge_quality_rows_skips_malformed_and_foreign(tmp_path):
    _write_jq(tmp_path / "good", FM)
    _write_jq(tmp_path / "broken", "not a frontmatter file at all\n")
    _write_jq(tmp_path / "foreign", '---\ntype: "something_else"\nbundle: z\n---\n')
    rows = judge_quality_rows(tmp_path)
    assert [r.bundle for r in rows] == ["good"]  # broken + foreign skipped


def test_judge_quality_rows_missing_rates_and_pack(tmp_path):
    # an old judge_quality.md without pack / the 3 new rates → PACK_UNKNOWN + None rates
    _write_jq(
        tmp_path / "old",
        '---\ntype: "judge_quality"\nbundle: old\njudge_model: "j"\n'
        "mean_abs_delta: 0.4\nnames_improvement_rate: 0.5\n---\n",
    )
    rows = judge_quality_rows(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r.pack == PACK_UNKNOWN
    assert r.cites_evidence_rate is None and r.justifies_level_rate is None


def test_judge_quality_rows_skips_trash(tmp_path):
    _write_jq(tmp_path / "live", FM)
    _write_jq(tmp_path / ".trash" / "deleted", FM)
    rows = judge_quality_rows(tmp_path)
    assert [r.bundle for r in rows] == ["b1"]  # the .trash copy is skipped (bundle field b1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_judge_compare.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'touchstone.gui.judge_compare'`.

- [ ] **Step 3: Create the module (parser + rows)**

Create `touchstone/gui/judge_compare.py`:

```python
"""Cross-judge aggregate (sub-project 2): discover per-bundle judge_quality.md results,
parse their YAML frontmatter, and (in group_by_pack) compute per-metric winners within a
pack. Pure + server-free (no fastapi/jinja) — mirrors gui/meta_report.py / aggregate.py."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PACK_UNKNOWN = "(Pack unbekannt — vor SP2 erzeugt, neu einlesen)"


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Parse a leading ``---\\n…\\n---`` YAML frontmatter block. Returns the mapping, or
    None if there is no frontmatter / it is not a mapping / the YAML is malformed. Never raises."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _as_float(v: Any) -> float | None:
    """A numeric frontmatter value → float; missing/null/non-numeric → None."""
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


@dataclass
class JudgeQualityRow:
    bundle: str
    pack: str
    judge_model: str
    mean_abs_delta: float | None
    names_improvement_rate: float | None
    cites_evidence_rate: float | None
    justifies_level_rate: float | None
    catches_safety_rate: float | None

    @property
    def key(self) -> str:
        return f"{self.bundle}|{self.judge_model}"


def judge_quality_rows(runs_dir: Path) -> list[JudgeQualityRow]:
    """Discover every judge_quality.md under runs_dir, parse its frontmatter into a row.
    Skips trashed bundles, files without judge_quality frontmatter, and unreadable files."""
    rows: list[JudgeQualityRow] = []
    for p in sorted(runs_dir.rglob("judge_quality.md")):
        if ".trash" in p.parts:
            continue  # a deleted bundle must not appear in the comparison
        try:
            fm = parse_frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm is None or fm.get("type") != "judge_quality":
            continue
        rows.append(
            JudgeQualityRow(
                bundle=str(fm.get("bundle") or p.parent.name),
                pack=str(fm.get("pack") or PACK_UNKNOWN),
                judge_model=str(fm.get("judge_model") or "—"),
                mean_abs_delta=_as_float(fm.get("mean_abs_delta")),
                names_improvement_rate=_as_float(fm.get("names_improvement_rate")),
                cites_evidence_rate=_as_float(fm.get("cites_evidence_rate")),
                justifies_level_rate=_as_float(fm.get("justifies_level_rate")),
                catches_safety_rate=_as_float(fm.get("catches_safety_rate")),
            )
        )
    rows.sort(key=lambda r: (r.pack, r.bundle, r.judge_model))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_judge_compare.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/judge_compare.py tests/test_judge_compare.py
git commit -m "feat(judge_compare): frontmatter parser + judge_quality.md row discovery"
```

---

### Task 3: Group by pack + per-metric winners

**Files:**
- Modify: `touchstone/gui/judge_compare.py` (append `PackGroup`, `_winner`, `group_by_pack`)
- Test: `tests/test_judge_compare.py` (add tests)

**Interfaces:**
- Consumes: `JudgeQualityRow` (from Task 2).
- Produces: `PackGroup` dataclass (`pack: str`, `rows: list[JudgeQualityRow]`, `winners: dict[str, str | None]`); `group_by_pack(rows: list[JudgeQualityRow]) -> list[PackGroup]`. `winners` keys: `"mean_abs_delta"`, `"names_improvement_rate"`, `"cites_evidence_rate"`, `"justifies_level_rate"`, `"catches_safety_rate"`; each value is the winning row's `.key` or `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_judge_compare.py`:

```python
from touchstone.gui.judge_compare import PackGroup, group_by_pack  # noqa: E402


def _row(bundle, pack, judge, mad, ni=0.5, ce=0.5, jl=0.5, cs=0.5):
    return JudgeQualityRow(bundle, pack, judge, mad, ni, ce, jl, cs)


def test_group_by_pack_splits_and_picks_winners():
    rows = [
        _row("b1", "ndassist", "judgeA", 0.5, ce=0.8),
        _row("b2", "ndassist", "judgeB", 0.2, ce=0.4),  # lower mean|Δ| → calibration winner
        _row("b3", "buero", "judgeA", 0.9),
    ]
    groups = group_by_pack(rows)
    assert [g.pack for g in groups] == ["buero", "ndassist"]  # sorted by pack
    nd = next(g for g in groups if g.pack == "ndassist")
    assert nd.winners["mean_abs_delta"] == "b2|judgeB"  # lower is better
    assert nd.winners["cites_evidence_rate"] == "b1|judgeA"  # higher is better
    buero = next(g for g in groups if g.pack == "buero")
    assert buero.winners["mean_abs_delta"] == "b3|judgeA"  # sole row wins its group


def test_group_by_pack_tie_no_winner():
    rows = [_row("b1", "p", "jA", 0.5), _row("b2", "p", "jB", 0.5)]  # equal mean|Δ|
    g = group_by_pack(rows)[0]
    assert g.winners["mean_abs_delta"] is None  # tie → no trophy


def test_group_by_pack_all_none_metric_no_winner():
    rows = [
        JudgeQualityRow("b1", "p", "jA", None, None, None, None, None),
        JudgeQualityRow("b2", "p", "jB", 0.5, None, None, None, None),
    ]
    g = group_by_pack(rows)[0]
    assert g.winners["cites_evidence_rate"] is None  # no scored values in this column
    assert g.winners["mean_abs_delta"] == "b2|jB"  # only b2 has a value → sole winner
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_judge_compare.py -q -k group_by_pack`
Expected: FAIL with `ImportError: cannot import name 'group_by_pack'`.

- [ ] **Step 3: Append the grouping + winners logic**

Append to `touchstone/gui/judge_compare.py`:

```python
_HIGHER_IS_BETTER = [
    "names_improvement_rate",
    "cites_evidence_rate",
    "justifies_level_rate",
    "catches_safety_rate",
]


@dataclass
class PackGroup:
    pack: str
    rows: list[JudgeQualityRow]
    winners: dict[str, str | None]


def _winner(rows: list[JudgeQualityRow], attr: str, higher: bool) -> str | None:
    """The row.key with the best (max if higher else min) non-None value of attr, or None
    if no row has a value or there is a tie (mirrors aggregate._winner: tie → no trophy)."""
    scored = [(getattr(r, attr), r.key) for r in rows if getattr(r, attr) is not None]
    if not scored:
        return None
    best = (max if higher else min)(v for v, _ in scored)
    leaders = [k for v, k in scored if v == best]
    return leaders[0] if len(leaders) == 1 else None


def group_by_pack(rows: list[JudgeQualityRow]) -> list[PackGroup]:
    """Group rows by pack (only same-pack rows are comparable) and pick a per-metric winner
    within each group: mean_abs_delta lower = better, the rubric rates higher = better."""
    by: dict[str, list[JudgeQualityRow]] = {}
    for r in rows:
        by.setdefault(r.pack, []).append(r)
    groups: list[PackGroup] = []
    for pack in sorted(by):
        grp = by[pack]
        winners: dict[str, str | None] = {"mean_abs_delta": _winner(grp, "mean_abs_delta", False)}
        for metric in _HIGHER_IS_BETTER:
            winners[metric] = _winner(grp, metric, True)
        groups.append(PackGroup(pack=pack, rows=grp, winners=winners))
    return groups
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_judge_compare.py -q`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/judge_compare.py tests/test_judge_compare.py
git commit -m "feat(judge_compare): group_by_pack + per-metric winners (tie → no trophy)"
```

---

### Task 4: Route + template + link from /compare

**Files:**
- Modify: `touchstone/gui/app.py` (add `GET /compare-judges` inside `create_app()`, near the `/compare` route ~app.py:383)
- Create: `touchstone/gui/templates/compare_judges.html`
- Modify: `touchstone/gui/templates/compare.html` (link in the page header)
- Test: `tests/test_gui_compare_judges.py` (create)

**Interfaces:**
- Consumes: `judge_compare.judge_quality_rows(runs_dir)`, `judge_compare.group_by_pack(rows)` (from Tasks 2–3); `render(name, request, **ctx)` helper.
- Produces: `GET /compare-judges` (HTML).

- [ ] **Step 1: Write the failing route tests**

Create `tests/test_gui_compare_judges.py`:

```python
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry


class _L:
    def spawn(self, a):
        return 1

    def alive(self, p):
        return False

    def terminate(self, p):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_L())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _jq(d, bundle, pack, judge, mad, ni, ce, jl, cs):
    d.mkdir(parents=True, exist_ok=True)
    (d / "judge_quality.md").write_text(
        f'---\ntype: "judge_quality"\nbundle: {bundle}\npack: {pack}\n'
        f'judge_model: "{judge}"\nmean_abs_delta: {mad}\nnames_improvement_rate: {ni}\n'
        f"cites_evidence_rate: {ce}\njustifies_level_rate: {jl}\ncatches_safety_rate: {cs}\n---\n# x\n",
        encoding="utf-8",
    )


def test_compare_judges_renders_grouped_table(tmp_path):
    _jq(tmp_path / "b1", "b1", "ndassist", "judgeA", 0.5, 0.7, 0.8, 0.6, 0.9)
    _jq(tmp_path / "b2", "b2", "ndassist", "judgeB", 0.2, 0.4, 0.4, 0.5, 0.5)
    r = _client(tmp_path).get("/compare-judges")
    assert r.status_code == 200
    assert "ndassist" in r.text
    assert "judgeA" in r.text and "judgeB" in r.text
    assert "🏆" in r.text  # at least one per-metric winner highlighted


def test_compare_judges_empty_state(tmp_path):
    r = _client(tmp_path).get("/compare-judges")
    assert r.status_code == 200
    assert "judge_quality" in r.text.lower() or "noch keine" in r.text.lower()


def test_compare_judges_tolerates_malformed_file(tmp_path):
    _jq(tmp_path / "ok", "ok", "p", "j", 0.5, 0.5, 0.5, 0.5, 0.5)
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "judge_quality.md").write_text("garbage, no frontmatter\n", encoding="utf-8")
    r = _client(tmp_path).get("/compare-judges")
    assert r.status_code == 200  # malformed file skipped, never 500
    assert "judge_quality" in r.text.lower() or "ok" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_compare_judges.py -q`
Expected: FAIL — `/compare-judges` returns 404 (route not registered).

- [ ] **Step 3: Add the route**

In `touchstone/gui/app.py`, inside `create_app()`, immediately after the `compare_cross` function ends (the `/compare` route, ~app.py:383), insert:

```python
    @app.get("/compare-judges", response_class=HTMLResponse)
    def compare_judges(request: Request) -> HTMLResponse:
        """Cross-judge aggregate: judge_quality.md across bundles, grouped by pack, with a
        per-metric winner per group. Read-only; scans runs_dir (no user path param)."""
        from touchstone.gui import judge_compare

        groups = judge_compare.group_by_pack(judge_compare.judge_quality_rows(runs_dir))
        return render("compare_judges.html", request, groups=groups, active="compare")
```

- [ ] **Step 4: Create the template**

Create `touchstone/gui/templates/compare_judges.html`:

```html
{% extends "base.html" %}
{% block title_suffix %} · Judge-Vergleich{% endblock %}
{% block breadcrumb %}
<nav class="breadcrumb muted text-sm"><a href="/compare">Vergleich</a> › Judge-Qualität</nav>
{% endblock %}
{% block body %}
<div class="page-header">
  <h1 class="page-title">Judge-Qualität vergleichen</h1>
  <span class="muted text-sm">welches Modell ist der bessere Judge — pro Pack</span>
</div>

{% if not groups %}
<div class="card">
  <p class="muted">Noch keine <code>judge_quality.md</code> vorhanden. Erzeuge sie über die Karte
  „Judge-Qualität (Meta-Eval)" auf einer Ergebnisseite (judge-meta Export → Cloud-KI → Einlesen).</p>
</div>
{% else %}
{% for g in groups %}
<div class="card">
  <div class="card-title">Pack: {{ g.pack }}</div>
  <p class="muted text-xs" style="margin-bottom:0.5rem">
    Vergleichbar nur bei gleichem Pack + gleicher Cloud-Referenz. 🏆 = bester Wert in dieser Spalte
    (niedrigstes mean|Δ|, höchste Raten); Gleichstand → keine Markierung.
  </p>
  <table class="bundle-table">
    <thead>
      <tr>
        <th>Bundle</th>
        <th>Judge-Modell</th>
        <th>mean|Δ|</th>
        <th>Verbesserung benannt</th>
        <th>Belege</th>
        <th>Höhe begründet</th>
        <th>Sicherheit</th>
      </tr>
    </thead>
    <tbody>
      {% for r in g.rows %}
      <tr>
        <td>{{ r.bundle }}</td>
        <td>{{ r.judge_model }}</td>
        <td>{% if r.mean_abs_delta is not none %}{{ r.mean_abs_delta }}{% if g.winners.mean_abs_delta == r.key %} 🏆{% endif %}{% else %}<span class="muted">—</span>{% endif %}</td>
        <td>{% if r.names_improvement_rate is not none %}{{ (r.names_improvement_rate * 100) | round | int }}%{% if g.winners.names_improvement_rate == r.key %} 🏆{% endif %}{% else %}<span class="muted">—</span>{% endif %}</td>
        <td>{% if r.cites_evidence_rate is not none %}{{ (r.cites_evidence_rate * 100) | round | int }}%{% if g.winners.cites_evidence_rate == r.key %} 🏆{% endif %}{% else %}<span class="muted">—</span>{% endif %}</td>
        <td>{% if r.justifies_level_rate is not none %}{{ (r.justifies_level_rate * 100) | round | int }}%{% if g.winners.justifies_level_rate == r.key %} 🏆{% endif %}{% else %}<span class="muted">—</span>{% endif %}</td>
        <td>{% if r.catches_safety_rate is not none %}{{ (r.catches_safety_rate * 100) | round | int }}%{% if g.winners.catches_safety_rate == r.key %} 🏆{% endif %}{% else %}<span class="muted">—</span>{% endif %}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endfor %}
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Add the link on /compare**

In `touchstone/gui/templates/compare.html`, change the page header block (lines 5–8) from:

```html
<div class="page-header">
  <h1 class="page-title">Vergleich</h1>
  <span class="muted text-sm">Läufe auswählen und vergleichen</span>
</div>
```

to:

```html
<div class="page-header">
  <h1 class="page-title">Vergleich</h1>
  <span class="muted text-sm">Läufe auswählen und vergleichen</span>
  <a href="/compare-judges" class="btn btn-secondary" style="margin-left:auto">Judge-Qualität vergleichen →</a>
</div>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_compare_judges.py -q`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add touchstone/gui/app.py touchstone/gui/templates/compare_judges.html touchstone/gui/templates/compare.html tests/test_gui_compare_judges.py
git commit -m "feat(gui): /compare-judges cross-judge aggregate view + link from /compare"
```

---

### Task 5: Gate + headless smoke (exit criteria)

**Files:** none (verification only).

- [ ] **Step 1: Full suite** — Run: `uv run pytest -q` → all pre-existing + new tests green.
- [ ] **Step 2: Types** — Run: `uv run mypy touchstone/` → no errors.
- [ ] **Step 3: Lint + format** — Run: `uv run ruff check . && uv run ruff format --check .` → clean (if format flags a file, `uv run ruff format .` and amend).
- [ ] **Step 4: Headless smoke (GUI restarted).** Re-ingest 1–2 real judged bundles through the enriched renderer so their judge_quality.md carries the new frontmatter (the GUI judge-meta card → paste a filled response, OR `touchstone judge-meta ingest`), then start a fresh server and confirm the view:

```bash
# regenerate enriched judge_quality.md for a real judged bundle (CLI path, no GUI needed):
#   uv run touchstone judge-meta export runs/<judged_bundle>
#   (fill runs/<judged_bundle>/judge_meta_response.yaml)
#   uv run touchstone judge-meta ingest runs/<judged_bundle>
# then, against a fresh server:
curl -s "http://127.0.0.1:<port>/compare-judges" | grep -o "Pack: [^<]*" | head
curl -s "http://127.0.0.1:<port>/compare-judges" | grep -c "🏆"
```

Expected: the page groups by pack, lists the judge models, marks per-metric winners; an old judge_quality.md without the new fields shows "—" (no crash); a bundle with no judge_quality.md is simply absent.

- [ ] **Step 5: Adversarial whole-branch review (controller, 3 lenses) → fix confirmed findings → merge.** Focus: (1) `parse_frontmatter`/`judge_quality_rows` never raise on arbitrary `.md` garbage; (2) winner math (min for mean|Δ| / max for rates, None excluded, tie→None, only within pack); (3) frontmatter enrichment is additive + backward-compatible and the `render_judge_quality_md` BODY bytes are unchanged. Then merge `feat/cross-judge-aggregate` → `main` + push (no PR, solo repo).

---

## Self-Review

**1. Spec coverage:** Frontmatter enrichment (pack + 3 rates) → Task 1. `parse_frontmatter` + `JudgeQualityRow` + `judge_quality_rows` (skip malformed/foreign/trash, missing→None, PACK_UNKNOWN) → Task 2. `group_by_pack` + winners (lower mean|Δ| / higher rates, tie→None, within-pack) → Task 3. `/compare-judges` route (flat, not `/compare/judges`) + grouped template (rates ×100 as %, 🏆, comparability caveat, empty state) + link on `/compare` → Task 4. never-500/never-crash → Tasks 2–4 tests. Gate + smoke + adversarial review → Task 5. All spec sections mapped.

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; `<port>`/`<judged_bundle>` in Task 5 are runtime values for a manual smoke, not code placeholders.

**3. Type consistency:** `JudgeQualityRow` fields + `.key` identical across Tasks 2–4; `winners` dict keys (`mean_abs_delta`, `names_improvement_rate`, `cites_evidence_rate`, `justifies_level_rate`, `catches_safety_rate`) match between `group_by_pack` (Task 3) and the template (Task 4); `PACK_UNKNOWN` defined in Task 2, asserted in Task 2 tests; the frontmatter field names written in Task 1 (`pack`, `*_rate`) match exactly what `judge_quality_rows` reads in Task 2; rates are fractions 0..1 in Task 1's output and rendered `× 100` in Task 4. `render_judge_quality_md` uses `detail.get("pack")` (defensive) consistent with the existing no-pack test.
