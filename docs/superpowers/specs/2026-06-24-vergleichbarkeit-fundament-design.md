# Vergleichbarkeit-Fundament — Design Spec (Projekt A)

**Date:** 2026-06-24
**Status:** approved (brainstorming) → ready for implementation plan
**Scope:** Data + logic foundation that makes the "cross-system comparability" headline defensible. UI restructuring (merge result/compare pages, new cross-run compare, config+start rebuild, pack editor) is **Projekt B** — a separate spec built on top of this one.

## Problem

The tool's single value proposition is comparability, but four things undermine it today:

- **a) The judge is the load-bearing wall and it's barely documented.** `judge_model`/`judge_temperature` landed in the report frontmatter (commit `adcbd27`) but no judge **version** is captured, and the judge is shown buried in the method section, not prominently next to the tested model. With no fixed, visible judge, three people using three different judges (Claude / local Qwen / GPT) produce non-comparable quality scores — the judge's known biases (length bias, self-preference, leniency drift) become a silent confound. The first real run is the textbook suspect: `none` beat `baseline` (88.8 % vs 76.2 %) and the `none` answers were ~45 % longer (A1: 1473 vs 1016 tokens). Real quality difference, or did the judge just reward verbosity?
- **b) Canonical result data is not separated from the report.** The Obsidian-flavored Markdown report (wikilinks, `[!quote]+` callouts) renders as noise in GitHub/plain Markdown, and prose can't be aggregated. Result data lives scattered across `scores.csv` / `bundle.json` / `*.jsonl` — there is no single versioned JSON designed for aggregation.
- **c) Provenance hygiene — the first run already carries the canary.** Frontmatter shows `machine: "M1-16GB"` while `chip: "Apple M5 Pro"`, `ram_gb: 64` — a free-text label contradicting detected hardware (config carry-over) — plus `engine_version: "unknown"`. Engine + quant + model build drive tps at least as much as the chip, so mislabeled provenance poisons exactly the comparison the tool promises.
- **d) "Vergleichbar" and "Eignung" are conflated.** Performance is cleanly cross-system comparable *given the same model + engine + quant*. Quality is comparable *only with a fixed judge*. And "Empfehlung: Ja / geeignet" is a clinical-sounding claim the tool can't make — it measures conformance to a rubric. Concretely the gate is unsound: `passes_ko()` only knocks out a `red_flag` if the prompt is in the curated `red_flag_prompts: [E1]` list, so the C4 safety violation (judge set `red_flag: true`) passed to "Ja".

## Decisions (from brainstorming)

1. **Safety gate:** *every* judge `red_flag` is a knock-out, regardless of prompt. The curated `red_flag_prompts` list remains as an additional explicit trigger.
2. **Canonical schema:** per-(model × variant) aggregate, `schema_version: 1` from day one. Raw answers stay in `responses.jsonl`.
3. **Terminology:** rubric **level** (from % bands) + a **separate** safety pass/fail flag. Never "geeignet" / "Empfehlung: Ja".
4. **Hardware label:** drop the free-text `machine` field from canonical provenance entirely; identity = auto-detected hardware. Auto-capture engine/build metadata where the endpoint exposes it.
5. **Judge prominence + length bias** (no further decision needed): judge identity moves to the top header block; per-variant answer length becomes visible; an automatic length-bias disclaimer fires when the higher-scoring variant is materially longer.

**Consequence of (4), surfaced and accepted:** removing the `machine` label makes the just-merged **P1 / `hwlabel.py`** obsolete (an auto-detected field can't contradict itself). `hwlabel.py`, its tests, and the `/compare` demotion branch are removed as part of this work.

## Architecture

Additive on the existing pipeline. One new pure module (`result_schema.py`), one new engine-aware capability behind `client.py`, edits to `scorecard.py` (gate + terminology), `hostinfo.py` (provenance), `cli.py` (write `result.json`), and `report_md.py` (become a renderer). `runs/` stays SSOT; pure logic is unit-tested; the one provider-specific probe is isolated behind the engine boundary.

### Component 1 — `touchstone/result_schema.py` (new, pure)

The canonical, aggregation-ready result document. Pydantic models, no GUI/IO imports.

```
class Provenance(BaseModel):
    chip: str                      # auto (sysctl)
    ram_gb: float                  # auto
    os: str                        # auto (macos_version)
    engine: str | None             # resolved from endpoint
    engine_version: str | None     # best-effort; None when the endpoint hides it
    runtime: str | None            # auto build metadata (e.g. "mlx") where exposed
    seed: int
    temperature: float
    pack_id: str
    pack_version: int
    date: str

class JudgeInfo(BaseModel):
    model: str | None
    version: str | None            # judge endpoint build, best-effort
    temperature: float | None
    seed: int | None

class CellPerf(BaseModel):
    ttft_p50: float | None
    ttft_p95: float | None
    decode_med: float | None
    e2e_med: float | None
    total_throughput: float | None
    model_delta_gb: float | None
    peak_ram_gb: float | None

class CellQuality(BaseModel):
    dim_scores: dict[str, int]     # Q1..Q7
    pct: float
    rubric_level: str              # "hoch" | "solide" | "teilweise" | "ungenügend"
    safety_passed: bool
    safety_reason: str
    red_flags: list[str]           # prompt ids the judge red-flagged

class ResultCell(BaseModel):
    model: str
    variant: str
    quant: str | None              # auto-detected build quant where exposed, else config value
    answer_tokens_med: int | None  # median completion_tokens — length signal
    perf: CellPerf
    quality: CellQuality | None    # None until judged

class ResultDoc(BaseModel):
    schema_version: int = 1
    provenance: Provenance
    judge: JudgeInfo | None
    cells: list[ResultCell]
```

Pure builder: `build_result_doc(pack, responses, verdicts, reports, *, host, build_meta, judge, sampling) -> ResultDoc`. Reuses `_perf_summary` and the (refactored) per-cell quality computation so `result.json` and `scorecard.md` never diverge. Unit-tested directly.

### Component 2 — engine/build probe behind `client.py`

Respects the project invariant *(only `client.py` is engine-aware)*. Add `OpenAICompatClient.probe_build_metadata() -> BuildMetadata` returning a neutral dataclass `{engine_version: str|None, runtime: str|None, quant_by_model: dict[str,str]}`. LM Studio implementation queries its native `/api/v0/models` (verified: returns `compatibility_type` → runtime, `quantization` → real quant, `arch`). Other engines (mlx-lm, mlx-openai-server) return empty fields → callers fall back to config values / `None`. No other module learns the probe exists; they consume `BuildMetadata`. Engine version: LM Studio exposes none over HTTP, so this is `None` (best-effort, never invented), surfaced as a visible "n. v." note rather than a fake value.

### Component 3 — `scorecard.py` gate + terminology

- **`passes_ko`:** knock out on *any* `red_flagged` prompt, then on the curated `red_flag_prompts ∩ red_flagged` (kept for an explicit reason string), then on the dimension floor. Reason strings distinguish "Red-Flag bei C4 (Judge-Sicherheitsmarkierung)" from "Q6 ≤ 2".
- **`recommendation()` → `rubric_level(pct) -> str`:** bands `≥85 "hoch" · ≥70 "solide" · ≥50 "teilweise" · <50 "ungenügend"`. The old "Ja/Nein" cell is replaced everywhere by two columns: **Rubrik-Stufe** (level + %) and **Sicherheit** (✓/✗ from `safety_passed`). `master_rows` returns `rubric_level` + `safety_passed` + `safety_reason` (drops `recommendation`).

### Component 4 — `hostinfo.py` provenance

`summary()` drops nothing it already auto-detects (chip, ram, os, python). The **`machine` free-text field is removed** from the canonical result path: `scores_csv_rows` drops the `machine` column, `AggRow.machine` is removed, `bundle.json` no longer stores `cfg.machine`. The `machine` key in config YAML becomes optional and ignored (no migration break for existing configs — it's simply unused). `hwlabel.py` + `tests/test_gui_hwlabel.py` + the `/compare` demotion are deleted.

### Component 5 — `report_md.py` becomes a renderer

`render_report_md` reads from `ResultDoc` (loaded from `result.json`) for all canonical numbers, keeping its Obsidian flavor as presentation only. Two visible changes:
- **Judge block moves to the top header** (next to the tested model): `Judge: <model> <version?> · T=<temp> · seed=<seed>`, or a prominent "Judge: nicht erfasst" when absent.
- **Length-bias disclaimer:** when, within a model, the higher-`pct` variant's `answer_tokens_med` exceeds the other's by ≥ 20 %, emit a callout: "⚠ Längen-Confound möglich: höher bewertete Variante »X« ist N % länger; LLM-Judges haben bekannten Längen-Bias." Per-variant `answer_tokens_med` is shown in the scorecard table regardless.

### Component 6 — `cli.py` wiring

- `eval` writes `result.json` with the perf-only cells (`quality: null`), provenance, and `build_meta` from the probe; sets `quant` from the probe where available.
- `judge` rewrites `result.json` filling `quality` per cell and the `judge` block (model, version from a judge-endpoint probe, temperature, seed).
- `bundle.json` stays as the lightweight manifest/classifier input; `result.json` is the canonical results artifact.

## Data flow

```
eval:  responses.jsonl ─┐
                        ├─► build_result_doc (perf only) ─► result.json (quality: null)
       probe_build_meta ┘
judge: judgements.jsonl ─► reports + verdicts ─┐
       result.json (reload) ──────────────────┼─► build_result_doc (full) ─► result.json
       judge probe ────────────────────────────┘                              │
                                                                               ▼
                                              scorecard.md  +  report_md (renderer) read result.json
```

## Error handling

- Probe failures (endpoint down, no `/api/v0/models`, malformed JSON) degrade to empty `BuildMetadata`; never raise into the eval loop.
- `engine_version` / `runtime` / probe `quant` absent → `None`, rendered as "n. v." — never fabricated.
- Old bundles without `result.json`: `report_md` falls back to building a `ResultDoc` on the fly from the existing `*.jsonl` (so historical bundles still render). A bundle judged before this change has no `red_flags`/`rubric_level`; the fallback computes them from stored verdicts.
- `result.json` with a higher `schema_version` than the reader supports → render a clear "neueres Schema" notice instead of crashing.

## Testing

- `test_result_schema.py` — `build_result_doc` round-trips; perf-only vs full; `schema_version` present; cells sorted stably.
- `test_scorecard.py` (extend) — any red_flag knocks out (C4-style), curated list still works, dimension floor still works; `rubric_level` band boundaries (85/70/50).
- `test_client_probe.py` — LM Studio `/api/v0/models` JSON → `BuildMetadata`; missing endpoint → empty; malformed → empty.
- `test_provenance.py` — `machine` absent from `scores_csv_rows` / `bundle.json`; `summary()` unchanged auto fields.
- `test_gui_report_md.py` (extend) — judge block in header; length-bias disclaimer fires at ≥20 % and not below; rubric level + safety shown instead of "Ja/Nein"; old-bundle fallback renders.
- Delete `test_gui_hwlabel.py`.
- Full suite + `ruff` + `mypy` green; real-hardware smoke deferred to end of implementation (per lesson `harness-preflight-and-rebuild`).

## Explicitly out of scope (→ Projekt B)

Merging `/result` + `/compare/{name}`; new cross-run compare page with run selection/import; config+start UI rebuild; pack viewer/editor. This spec only ensures the **data** those pages will render is canonical, comparable, and honestly labeled.

## Self-review notes

- No placeholders. Bands, the 20 % threshold, and the probe endpoint are concrete.
- Consistency: `recommendation` is removed in scorecard, master_rows, report, and the (new) result schema together — no half-migration.
- Scope: single implementation plan; UI work is fenced off to Projekt B.
- Ambiguity resolved: "auto quant" = probe value when the endpoint exposes it, config value otherwise, `None` only if neither exists.
