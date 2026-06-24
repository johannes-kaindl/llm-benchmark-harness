# GUI Best-Practices Redesign — Design

**Date:** 2026-06-23
**Status:** approved (design), implementation pending
**Branch:** `feat/gui-best-practices-redesign`
**Scope owner decision:** one spec, all 7 packages, phased; autonomous execution.

---

## 1. Problem & strategic decision

The `ramcheck gui` control-center *works* but feels raw and unprofessional. A grounded
8-area code survey (workflow `map-gui-current-state`, 2026-06-23) classified ~38 distinct
user complaints by root cause:

| Root cause | Count | Nature |
|---|---|---|
| missing-feature | ~22 | **data already captured, just not rendered** + a few genuinely new features |
| missing-explanation | ~8 | raw fields shown with no label/legend/tooltip |
| methodology / data-not-captured | ~7 | RAM definition, reasoning-phase timing |
| **bug** | **1** | hardware-label contradiction |
| **framework** | **0** | nothing is blocked by the stack |

**Decision: do NOT switch GUI frameworks.** Every one of the 8 areas returned the same
framework verdict — FastAPI + Jinja2 + Alpine.js + hand-written CSS is well-matched to the
**out-of-process control-plane** architecture (thin server, `runs/` as SSOT, build-free
assets, native SSE). HTMX is even bundled-but-unused dead weight. A rewrite to
Streamlit/Gradio/SPA would *delete* current strengths (offline file export, browser-config
workflow, decoupled measurement) and *add* new problems (hydration, API tax) to fix problems
the stack does not have. **The work is additive: a component library, an explanation layer,
a few new routes, one real data-model fix, and two small instrumentation additions.**

---

## 2. Architecture spine (shared by all packages)

Two cross-cutting substrates are built first because every package consumes them.

### 2.1 Jinja macro library — `ramcheck/gui/templates/macros/`

A small set of reusable presentation macros so stations stop being ad-hoc:

- `metric(label, value, key=None, unit=None)` — renders a value with its label and an
  info affordance (tooltip/`?`) whose text comes from the glossary (§2.2) when `key` is set.
- `badge(text, kind)` — verdict/status badges (reuse existing CSS vars).
- `card(title)` / `collapsible(summary)` — bordered section + Alpine-driven expand block
  (mirrors the existing `_method_explainer.html` toggle pattern).
- `alert(kind, body)` — consistent error/warning/info block (replaces inline-styled divs).
- `kv_row(label, value)` — definition-list row for detail views (config/pack viewers).

All 8 templates migrate to these macros incrementally as each package touches them. No
template keeps a bespoke version of a pattern that has a macro.

### 2.2 Metric glossary — `ramcheck/gui/glossary.py`

**Single source of truth** for what every metric/label means. A pure `dict[str, Glossary]`
mapping a stable key → `{term, short, long}` (German). Covers at minimum:
`quality_pct`, `ttft_p50`, `decode_median`, `prefill_tps`, `e2e`, `total_throughput`,
`system_peak_ram`, `model_delta_ram`, `mem_pressure`, `variant_baseline`, `variant_none`,
`reasoning_only`, `reasoning_duration`, `reasoning_tps`, `cold_start`, `cpu`.

- Pure module, no GUI imports → unit-testable.
- A test asserts **every metric key rendered by a template exists in the glossary** (no
  unexplained metric can ship). This is the structural guarantee behind "self-explaining UI".
- The `metric()` macro pulls `short` for the tooltip and links to a `/glossary` page (or an
  expandable on `_method_explainer.html`) for `long`.

---

## 3. The seven packages

Sequence: **P1 → P2 → P3 → (P4 ∥ P7) → P5 → P6 → P0.**

### P1 — The bug: hardware-label contradiction *(easy, first)*

**Symptom:** a compare row shows Chip "Apple M5 Pro" / RAM "64.0 GB" but Maschine "M1-16GB".

**Cause:** `chip` / `ram_gb` come from live `hostinfo.summary()` at eval time
(`scorecard.py:298-299`); the `machine` column is a *static* `Config.machine` YAML label
carried through `responses.jsonl`. Reuse a config on new hardware without editing the label →
live truth collides with a stale hand-typed label in the same `scores.csv` row.

**Fix (display, immediate):** in the compare views prefer detected `chip`/`ram_gb`; render a
stale/mismatching `machine` label muted with a mismatch warning instead of trusting it.
**Fix (durable):** at scorecard-write time, validate `machine` against detected chip+ram and
warn (or auto-annotate) on mismatch. Files: `ramcheck/gui/compare.py`, `compare.html`,
`compare_axis.html`, `ramcheck/scorecard.py`.

**Tests:** pure-logic test for the mismatch detector (detected vs label) → expected
display/flag state.

### P2 — Explain-everything layer *(easy, highest leverage)*

Roll out §2.1 macros + §2.2 glossary across the stations:

- Metric headers in `compare.html` / `compare_axis.html` / `result.html` carry glossary
  tooltips (QUALITÄT %, TTFT P50, Decode, System-Peak, Modell-Delta, …).
- Model-checkbox help in `config.html`: "Nur angehakte Modelle werden evaluiert."
- `none`/`baseline` legend rendered from `pack.prompt_variants` (id + system-prompt summary).
- Pack name in cross-run `compare.html` becomes a hyperlink to `/packs/packs/{pack}.yaml`
  (within-bundle already links).
- Breadcrumbs above page headers (Übersicht > Ergebnis > {name}, etc.).
- `_method_explainer.html` reformatted: weighting formula in a `<pre>`/code block, the two
  K.-o. branches as bordered cards, the 1–5 scale as a small table.
- Remove the unused HTMX bundle (drop from `pyproject.toml [gui]` + the script tag).

**Tests:** glossary-completeness test (§2.2); template render-smoke tests stay green.

### P3 — Result perf panel + full text *(easy, render-only)*

The per-answer data is **already in `responses.jsonl`** — this is pure rendering.

- Per answer in `result.html` (the per-answer loop ~`result.html:318-356`): a metrics block
  showing `ttft_s`, `decode_tps`, `prefill_tps`, `e2e_s`, `prompt_tokens`,
  `completion_tokens`, and **total throughput** = `(prompt_tokens + completion_tokens) / e2e_s`
  (computed in the view layer / `bundles.py`), each via the `metric()` macro.
- `pack.html`: render the full `PackPrompt.prompt` text (currently omitted) and the full
  `PromptVariant.system_prompt` (currently sliced to 80 chars at `pack.html:40`), collapsible,
  `white-space:pre-wrap` — mirror the `result.html` pre-wrap pattern.
- Reasoning: show `reasoning_chars` for all answers (not only empty ones) and an expandable
  `reasoning_text` block when persisted.
- **Backend rider:** add `e2e_s` (as `e2e_med`) to `scores.csv` so cross-run total-throughput
  survives the aggregate path (`scorecard.py:305-307` + `aggregate.py`). This is the one
  real data-loss fix on the aggregate path.

**Tests:** `scorecard`/`aggregate` test for the new `e2e_med` column; total-throughput
derivation unit test.

### P4 — Config/Pack viewer + ephemeral overrides + YAML export *(medium)*

User goal: *ansehen, anpassen, exportieren* for both pack and config. Chosen depth:
**read-only viewer + ephemeral start-overrides + YAML download** (no write-back editor; the
YAML file stays SSOT).

- **View:** new route `/config/{config_path:path}` mirroring `/packs/{pack_path}`
  (`app.py:77-87`). Renders the `Config` fields read-only via `kv_row` macros: endpoint,
  machine, scenarios, context_buckets, runs_per_cell, max_tokens, sampling, power_check,
  embed/vlm specs. A "?" link next to the config picker opens it.
- **Adjust (ephemeral):** extend the existing model-override mechanism
  (`config.models_from_json` / `apply_models_override`) to a small whitelist of safe
  start-time overrides (e.g. `runs_per_cell`, selected models, seed/temp) submitted with the
  run. Pure logic, validated, **nothing written to disk** — the bundle records what actually
  ran (consistent with the existing G-comment design).
- **Export:** a "Download YAML" action that streams the *effective* pack/config YAML (after
  overrides applied) so a run is reproducible on another machine.

**Tests:** route returns expected fields for a sample config; override-whitelist parser
(valid → applied, unknown key → rejected, nothing persisted); effective-YAML serialization.

### P5 — RAM methodology: baseline-subtracted model delta *(medium)*

Today peak RAM = `max(sys_used_mb)` over the window = whole-machine memory (OS + cache +
everything), no baseline → not cross-machine comparable, and the "Peak-RAM" label oversells
it. Chosen depth: **baseline-delta as the comparable headline + honest relabeling**; per-prompt
KV/context isolation documented as a future stage (hard/approximate on unified memory).

- **Capture:** one baseline `sys_used_mb` sample **before** `sampler.start()` (before the
  model is loaded / weights mmap'd). `sampler.py:239-251` / the eval+benchmark start path.
  Persist it (baseline marker in `resources.jsonl` or a small `baseline` field).
- **Derive:** `sys_used_delta_mb = peak_sys_used_mb − baseline_sys_used_mb` in `merge.py`;
  add the column to `models.py` (`RAW_CSV_COLUMNS` — change one, change both; assert holds)
  and `scores.csv`.
- **Relabel UI:** "System-Peak" (includes OS/background), "Modell-Delta" (baseline-subtracted,
  cross-machine-comparable headline), "Memory-Pressure". Glossary entries + tooltips.
- **Document:** `AGENTS.md` gotchas + `docs/explanation/design-decisions.md`: Apple-Silicon
  unified memory, why baseline subtraction, what is *not* decomposed (KV-cache), residual
  uncertainty. Per-prompt context/KV delta = explicit future stage.

**Tests:** `test_merge` case for baseline-subtracted aggregation (locks the contract);
`RAW_CSV_COLUMNS` ↔ `RunRecord` assertion stays green.

### P6 — Reasoning-phase timing *(medium-hard, opt-in, last before rename)*

Thinking duration/tokens/tps are **not captured** today — only `reasoning_chars`. This is the
one genuinely invasive package (hot streaming path + schema + tests).

- **Instrument** `stream_once` (`runner.py:88-164`): record `t_reasoning_start` (first
  reasoning chunk) and the wall-clock of the last reasoning chunk → `reasoning_duration_s`.
  Apply the existing heuristic token counter to `reasoning_text` → `reasoning_completion_tokens`
  → `reasoning_tps = reasoning_completion_tokens / reasoning_duration_s`.
- **Schema:** add the fields to `RequestOutcome` / `EvalResponse` (`results.py:15-56`) and
  persist to `responses.jsonl` (`qualrun.py:189-207`). Keep `responses.jsonl` slim — same
  policy as `reasoning_text` (timing always; text only when `content_empty`).
- **Surface:** per-answer in `result.html` (thinking vs response split) and a reasoning column
  in `compare.py`/`compare_axis.html` (compare reasoning overhead across models).

**Tests:** `stream_once` timing test with a fabricated reasoning-then-content stream
(dependency-injected clock — pattern already used); schema round-trip; `derive_rates`-style
nan-safety for zero-duration reasoning.

### P7 — Export/import whole bundles *(easy, independent, parallel to P4)*

Unlocks multi-machine comparison.

- `GET /export-bundle/{name}` → zip the canonical ledger (`bundle.json`, `responses.jsonl`,
  `scores.csv`, `reports.jsonl`, `judgements.jsonl`, `scorecard.md`, `perf.csv`,
  `resources.jsonl`); **exclude transient sentinels** (`run.json`, `events.jsonl`,
  `judge_events.jsonl`) — consistent with the existing `/export` allowlist.
- `POST /import-bundle` → multipart upload → validate (`bundle.json` parses,
  `responses.jsonl` valid JSONL) → land in `runs/` with a collision-safe name → `aggregate`
  picks it up automatically (it already rglobs `scores.csv`).
- **Identity/dedup:** run dirs are `timestamp_eval_pack` → two machines can collide on the
  same second. Default: append a short host/seq suffix on import; warn (don't block) when
  `pack_path` is absent locally. A help card explains portability + identity.
- "Download as zip" button alongside the per-file exports in `result.html`.

**Tests:** export builds the expected file set and excludes sentinels; import validation
(good zip lands; missing `bundle.json` rejected; name-collision gets suffixed).

### P0 — Tool rename *(last, target name TBD)*

A broad mechanical sweep (CLI command `ramcheck`, package `llm-ramcheck`, repo
`llm-benchmark-harness`, docs, configs). Deferred to the very end when the surface is stable.
**Blocked on:** the user providing the target name before execution.

---

## 4. Out of scope (explicitly deferred)

- Heavy polish: dark mode, mobile/hamburger reflow, micro-interactions, a `/styleguide` page.
- Per-prompt context/KV-cache RAM decomposition (P5 future stage).
- Full in-GUI YAML write-back editor (P4 chose ephemeral overrides instead).
- A `--runs a b c` multi-dir aggregate flag (import makes it unnecessary for the happy path).

---

## 5. Cross-cutting constraints

- **mypy strict + ruff** stay green; pure logic unit-tested, I/O dependency-injected.
- `models.RAW_CSV_COLUMNS` ↔ `RunRecord` import-time assertion must hold after P3/P5.
- No engine-specific branch outside `client.py`.
- **After every GUI change:** restart the server and verify routes headless (a stale process
  serves dead routes → 404 → looks "broken"). Lesson `gui-restart-after-changes`.
- **Before "done":** one real end-to-end smoke against a live local model — tests/review
  check logic, not real load. Lesson `harness-preflight-and-rebuild`.
- Solo repo: feature branch → merge to `main` + push (Codeberg→GitHub mirror), no PR.

---

## 6. Testing strategy summary

| Package | Key new tests |
|---|---|
| P1 | hardware mismatch detector |
| P2 | glossary completeness (every rendered metric key defined) |
| P3 | `e2e_med` in scores.csv; total-throughput derivation |
| P4 | config-view fields; override whitelist; effective-YAML export |
| P5 | merge baseline-delta; RAW_CSV_COLUMNS assertion |
| P6 | stream_once reasoning timing (injected clock); schema round-trip |
| P7 | export file-set/exclusions; import validation + collision suffix |

Existing template render-smoke + full `pytest`/`mypy`/`ruff` gates run throughout.
