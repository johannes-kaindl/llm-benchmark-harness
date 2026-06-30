# Decision Records (ADRs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Doc-drafting plan: ADRs are drafted via a Workflow fan-out, then **adversarially fact-verified against the code** before commit.

**Goal:** Establish a navigable ADR decision-record layer (`docs/decisions/`) capturing the harness's load-bearing decisions with context/alternatives/consequences, plus an index and a `docs/README.md` hero.

**Architecture:** One MADR-lite ADR per decision under `docs/decisions/`; an index `README.md` (load-bearing ADRs + spec-only backlog rows); a hero `docs/README.md` (Diátaxis map + architecture overview). The 8 `design-decisions.md` essays migrate into ADRs; `design-decisions.md` is trimmed to a linking narrative (keeps the AGENTS.md/GUI reference valid). ADRs are drafted by a Workflow fan-out from each decision's spec+code+essay, then each is fact-checked against the code before commit.

**Tech Stack:** Markdown docs · Python 3.12/uv (only for the link-check test) · Workflow tool (drafting + verification fan-out).

## Global Constraints

- **No code-behavior change.** Only `docs/**` + `AGENTS.md` (pointer text) change. `touchstone/` untouched.
- German prose (matches repo docs); code identifiers English. Ruff/mypy/pytest stay green.
- Solo repo: branch `feat/decision-records` (already created) → merge to `main` + push, **no PR**.
- ADR format = MADR-lite: `Status` · `Bereich` · `Kontext` · `Entscheidung` · `Erwogene Alternativen` · `Auswirkungen` · `Belege & Links`. Each section non-empty.
- **Every ADR must be fact-true against the code** — verified adversarially before commit (the spec's #1 risk).
- `design-decisions.md` stays (trimmed to narrative+links) so AGENTS.md:239 + `templates/_method_explainer.html` references don't break.

---

### Task 1: Scaffold — ADR template, directory, link-check test

**Files:**
- Create: `docs/decisions/0000-template.md`, `docs/decisions/README.md` (skeleton)
- Create: `scripts/check_doc_links.py`, `tests/test_doc_links.py`

**Interfaces:**
- Produces: `scripts/check_doc_links.py::broken_links(root: Path) -> list[str]` — returns `["<file> → <missing target>", …]` for every unresolved **relative** `.md` link under `docs/`. Empty list = all good.

- [ ] **Step 1: Write the ADR template** `docs/decisions/0000-template.md`

```markdown
# ADR-NNNN: <Titel>

- **Status:** akzeptiert · <YYYY-MM-DD>
- **Bereich:** <Architektur | Perf-Methodik | Quali-Eval | Judge | GUI | Betrieb>

## Kontext
<Problem + Kräfte/Constraints, die die Entscheidung erzwingen.>

## Entscheidung
<Was entschieden wurde — knapp, präzise.>

## Erwogene Alternativen
- **<Alternative>** — verworfen, weil <Grund>.

## Auswirkungen
- Positiv: <Konsequenz>.
- Trade-off / Restgrenze: <bewusst akzeptierter Nachteil>.

## Belege & Links
- Spec: `docs/superpowers/specs/<…>.md` · Code: `touchstone/<…>.py` · Tests: `tests/<…>.py`
- Verwandt: [ADR-XXXX](XXXX-<…>.md)
```

- [ ] **Step 2: Write the link-check** `scripts/check_doc_links.py`

```python
"""Check that every relative .md link under docs/ resolves to an existing file.
Pure stdlib; used by tests/test_doc_links.py and ad hoc."""

from __future__ import annotations

import re
from pathlib import Path

LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def broken_links(root: Path) -> list[str]:
    out: list[str] = []
    for md in root.rglob("*.md"):
        for m in LINK.finditer(md.read_text(encoding="utf-8")):
            target = m.group(1).split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (md.parent / target).resolve().exists():
                out.append(f"{md.relative_to(root)} → {target}")
    return out


if __name__ == "__main__":
    bad = broken_links(Path("docs"))
    print("\n".join(bad) if bad else "all doc links resolve")
    raise SystemExit(1 if bad else 0)
```

- [ ] **Step 3: Write the failing test** `tests/test_doc_links.py`

```python
from pathlib import Path

from scripts.check_doc_links import broken_links


def test_docs_have_no_broken_relative_links():
    bad = broken_links(Path("docs"))
    assert bad == [], "broken doc links:\n" + "\n".join(bad)
```

- [ ] **Step 4: Run it** — `uv run pytest tests/test_doc_links.py -q`
Expected: PASS (no decisions/README links yet, existing docs resolve). If an existing doc already has a broken relative link, fix that link first. Create `docs/decisions/README.md` with a one-line skeleton (`# Decision Records\n\n_(Index folgt.)_`) so the dir exists.

- [ ] **Step 5: Commit**

```bash
git add docs/decisions/0000-template.md docs/decisions/README.md scripts/check_doc_links.py tests/test_doc_links.py
git commit -m "docs(decisions): ADR template + dir + doc-link-check test"
```

---

### Task 2: Draft + fact-verify the load-bearing ADRs

**Files:** Create `docs/decisions/NNNN-*.md` (the table below). Source of truth for each = its spec + code + essay.

**ADR list (NNNN · title · primary code · spec/essay source):**

| NNNN | Titel | Code | Quelle |
|---|---|---|---|
| 0001 | OpenAI-kompatibel = einzige Schnittstelle | `client.py` | spec `2026-06-19-ndeval-harness-design`; essay §OpenAI |
| 0002 | Zwei entkoppelte Producer + Merge per Zeitfenster | `sampler.py`,`merge.py` | ndeval-spec; essay §entkoppelte Prozesse |
| 0003 | Verteilung statt Mittelwert (P50/P95, CV%) | `stats.py`,`report.py` | essay §Verteilung |
| 0004 | Modell-Delta vs System-Peak | `merge.py`,`report.py` | essay §Modell-Delta; AGENTS gotcha |
| 0005 | Warmup verworfen, Cold-Start separat | `runner.py` | essay §Warmup |
| 0006 | Quali-Eval-Teilung + Pack=Daten | `pack.py`,`qualrun.py`,`results.py` | AGENTS „qualitative half"; spec `2026-06-27-buero-pack-design` |
| 0007 | Master-Dimensionen holistisch + Nachvollziehbarkeit | `judge.py`,`scorecard.py` | essay §Master-Dimensionen; AGENTS reports.jsonl |
| 0008 | K.-o./Safety-Gate + red_flag_scope | `pack.py`,`scorecard.py` | spec `2026-06-27-ko-red-flag-scope-design` |
| 0009 | reasoning-only→unscored + Pre-Flight + free max_tokens | `judge.py`,`preflight.py`,`client.py` | spec `2026-06-22-thinking-models-blocker-design`; essay §reasoning-only |
| 0010 | Judge-Runaway-Guard | `judge.py`,`cli.py` | spec `2026-06-28-judge-runaway-guard-design` |
| 0011 | Thinking-Suppression (suppress_thinking) | `judge.py`,`cli.py` | spec `2026-06-28-judge-prompt-patch-design` (Rev.); explanation `judge-quality-meta-eval-2026-06-28` |
| 0012 | Judge-Prompt-Härtung (Rationale-Regeln, gescopte Safety-Reconciliation) | `judge.py` | spec `2026-06-28-judge-prompt-patch-design` |
| 0013 | Judge-Quality-Meta-Eval-Methode | `gui/judge_meta.py`,`cli.py` | spec `2026-06-26-judge-quality-meta-eval-design`; explanation doc |
| 0014 | GUI Out-of-Process-Control-Plane + runs/=SSOT | `gui/` | spec `2026-06-21-gui-steuerzentrale-design`; essay §GUI |
| 0015 | Nacht-Queue (ein Modell/Run, reset+settle, Watchdog, --check) | `runqueue.py`,`cli.py` | spec `2026-06-27-nacht-queue-design` |

- [ ] **Step 1: Draft via Workflow fan-out.** Run a workflow that, per ADR row, spawns one drafting agent: input = read the named spec + code file(s) + relevant `design-decisions.md` section; output (schema) = the MADR-lite fields (status/bereich/kontext/entscheidung/alternativen[]/auswirkungen[]/links). One stage. Each agent reads its own sources only. Collect the 15 drafts.

- [ ] **Step 2: Adversarial fact-verify each draft (pipeline stage 2).** For each draft, a second agent **reads the cited code** and checks every factual claim (default values, file names, behavior, the alternatives' accuracy); returns `{ok: bool, corrections: [...]}`. The controller (me) applies corrections. **No ADR is written to disk until its facts check out against the code.**

- [ ] **Step 3: Materialize the ADR files.** Write each verified ADR to `docs/decisions/NNNN-*.md` in the template format (deterministic write from the verified structured fields). Verify each file has all non-empty sections.

- [ ] **Step 4: Commit**

```bash
git add docs/decisions/0001-*.md  # … all 15
git commit -m "docs(decisions): 15 load-bearing ADRs (fact-verified against code)"
```

---

### Task 3: Index + Hero + link-check green

**Files:** Modify `docs/decisions/README.md`; Create `docs/README.md`.

- [ ] **Step 1: Fill the index** `docs/decisions/README.md` — intro sentence + table (ID · Titel · Status · Bereich · Links), one row per ADR 0001–0015, then **backlog rows** for every remaining `docs/superpowers/specs/*` not yet ADR'd (GUI features: web-monitor, gui-nachvollziehbarkeit, modell-auswahl/vergleich/dropdown, b1/b2/b3a, pack-editor, meta-report, overview-batch-trash, judge-meta-gui, cross-judge-aggregate, cross-run-aggregation) with Status `spec-only` + link to the spec.

- [ ] **Step 2: Write the hero** `docs/README.md` — one paragraph „Was ist touchstone"; a **Diátaxis map** (reference=`reference/metrics-and-schema.md` · explanation=`explanation/design-decisions.md` · decisions=`decisions/README.md` · history=`superpowers/specs/`); a compressed architecture overview (the module pipeline from AGENTS.md); a „Wo finde ich…?" wegweiser. All links relative + valid.

- [ ] **Step 3: Link-check green** — `uv run pytest tests/test_doc_links.py -q` → PASS (all index + hero links resolve). Fix any broken link.

- [ ] **Step 4: Commit**

```bash
git add docs/decisions/README.md docs/README.md
git commit -m "docs(decisions): index (ADRs + spec-only backlog) + docs hero/landing"
```

---

### Task 4: Trim design-decisions.md to narrative + update AGENTS.md

**Files:** Modify `docs/explanation/design-decisions.md`, `AGENTS.md`.

- [ ] **Step 1: Trim `design-decisions.md`** — keep its role as the Diátaxis „explanation": each of the 8 themed sections becomes 1–2 narrative sentences + a link to the corresponding ADR (the per-decision detail/alternatives now live in the ADR). Keep the holistic/traceability section's substance discoverable (AGENTS.md:239 + GUI method-explainer point here).

- [ ] **Step 2: Update `AGENTS.md`** — the `CORE-META-03/04` line (docs now: `docs/decisions/` ADRs + `docs/README.md` hero exist; only the hero *image* + tutorials/how-to remain) and the `docs/`-Hinweise (add `docs/decisions/`). Do not touch other AGENTS sections.

- [ ] **Step 3: Link-check + suite green** — `uv run pytest -q && uv run mypy touchstone/ && uv run ruff check .` → all PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/explanation/design-decisions.md AGENTS.md
git commit -m "docs(decisions): trim design-decisions to narrative+links; update AGENTS pointers"
```

---

### Task 5: Final review + merge

- [ ] **Step 1: Final adversarial whole-branch review** — workflow lenses: (1) **fact-truth** (re-check a sample of ADRs' claims against code — the #1 risk), (2) **dead links / completeness** (index covers all specs; hero links resolve), (3) **duplication** (design-decisions.md narrative doesn't duplicate ADR decision-detail). Controller verifies each finding. **Review agents must use read-only git (no checkout)** ([[workflow-agents-share-worktree]]).
- [ ] **Step 2: Fix confirmed findings**, re-run link-check + suite.
- [ ] **Step 3: Merge + push**

```bash
git checkout main && git merge --no-ff feat/decision-records
git push origin main
```

- [ ] **Step 4:** Update the `[[doku-initiative]]` memory (status: decision-layer shipped; remaining: tutorials/how-to/hero-image).

---

## Self-Review

- **Spec coverage:** ADR format → Task 1. ~13 load-bearing ADRs → Task 2 (15 listed). Index (ADRs+backlog) → Task 3 Step 1. Hero → Task 3 Step 2. Migration of essays + trim design-decisions.md + AGENTS pointer → Task 4. Fact-verification (spec's #1 risk) → Task 2 Step 2 + Task 5 Step 1. Link-check acceptance → Task 1 (test) + Tasks 3/4. No-code-change → Global Constraints. All spec sections mapped.
- **Placeholder scan:** none — template, link-check script, test, and the ADR source-mapping table are concrete.
- **Consistency:** `broken_links(root)` signature identical in script (Task 1 Step 2) + test (Step 3) + usage (Tasks 3/4). ADR numbering 0001–0015 consistent between Task 2 table and Task 3 index.
