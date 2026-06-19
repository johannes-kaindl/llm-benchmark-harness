# Design — Qualitative Use-Case Evaluation for `ramcheck`

**Status:** approved (brainstorming, 2026-06-19) · **Increment:** 1 of N
**Author:** Johannes + Claude Code (Opus 4.8)

## 1. Context & goal

`ramcheck` today is a *performance* harness: it drives a fixed prompt set through an
OpenAI-compatible endpoint and records TTFT / decode-tok/s / prefill / peak-RAM with
their distribution, plus macOS memory pressure & throttling — deterministically, with
auto-detected host specs (chip, RAM). Output is Markdown + CSV. The whole point: the
*same code* runs on different machines; only the config changes.

We are adding the **second half**: *qualitative* evaluation. A user wants to know not just
*how fast* a model runs on given hardware, but *how good its answers are* for a concrete
**use case** — starting with a **Neurodivergenz-Assistent** (ADHS/autism support). The two
halves combined answer: *"which model, on which hardware, gives me what quality at what
speed and RAM cost?"* — useful both personally and as a **publishable, community-comparable**
benchmark.

### Key design principle (the seam)

**Generation (deterministic, on the machine under test) is cleanly separated from
judging (non-deterministic, LLM or human, afterwards).** A run always produces the
deterministic artifacts (responses + perf/RAM specs + a fill-in scorecard); judging is an
optional second pass. This separation is what makes everything else fall out:

- **Tech-specs auto-filled, quality separate** — `eval` fills the hardware/perf half; quality
  cells stay blank until `judge` (or a human) fills them.
- **Modularity** — a *use case* (ND, office, …) is a **Pack**: data, not code.
- **Prompt-benchmarking** — a system-prompt variant is just another axis of the run matrix
  (model × prompt-variant × pack-prompt). Same model, different prompts → comparable scorecards.
- **Comparability** — standardized setup + pack + output makes foreign runs comparable.

## 2. Decisions (ratified in brainstorming)

1. **First increment** = qualitative-run capability + the ND pack + auto perf-specs, with
   LLM-judge as a separate step. (Not a throwaway script; not the full framework up front.)
2. **Approach A** = extend `ramcheck` in-repo with `eval` + `judge` subcommands and four new
   modules, reusing the existing client/sampler/merge/hostinfo/stats machinery.
3. **Judging** = pluggable; default a strong (cloud) judge for reliable scores; a local judge
   selectable for fully-offline runs; or skip judging and fill the scorecard manually.
4. **Sampling** = default **deterministic** (temperature 0 + fixed seed) for reproducible,
   cross-machine/cross-person comparable, re-judgeable runs; overridable per pack (a pack may
   opt into temp 0.7 + repeats for variance realism).
5. **Prompt-variant axis** present from the start (the ND pack ships `baseline` + `none`).

## 3. Non-goals (this increment)

- Cross-machine **merge/aggregation** UI (output is *designed to be* mergeable — `scores.csv` —
  but combining M1+M5 into one table is a later step).
- Additional packs (office-assistant, …) — only the ND pack ships now.
- HTML/web output; publish polish (README for external runners, CI matrix, etc.).
- No engine-specific code outside `client.py` (unchanged ramcheck rule).

## 4. Architecture

Two subcommands, four new modules, maximal reuse.

| Module | One purpose | Depends on |
|---|---|---|
| `pack.py` | Load + validate a use-case **Pack** (YAML) → typed `Pack` (pydantic). | yaml, pydantic |
| `qualrun.py` | Drive the eval matrix (model × variant × prompt × repeat), capture **response + perf record**, write the bundle. | `client`, `sampler`, `merge`, `runner`, `hostinfo`, `pack` |
| `judge.py` | Pluggable judge: per-response score vs. green/red flags + holistic master scorecard + K.-o. rule. | `client` (as judge endpoint), `pack` |
| `scorecard.py` | Render the filled scorecard markdown (ND-note structure) + machine-readable `scores.csv`. | `pack`, `stats` |

**Reused, not duplicated:** `client` (generation *and* judging), `sampler`+`merge`
(perf/RAM — the auto-filled tech-specs), `hostinfo` (chip/RAM header), `stats`
(distributions), `models`/`config`.

### Data flow — two decoupled phases

```
pack.yaml ─┐
           ├─► ramcheck eval ─► runs/<ts>_eval/
config.yaml┘     (generation +      ├─ responses.jsonl   (every answer, raw + per-request perf)
                  perf sampling)     ├─ perf.csv          (TTFT/decode/RAM per prompt)
                                     ├─ bundle.json       (run manifest: pack id+ver, models, host, config)
                                     └─ scorecard.md      (tech-specs filled, quality blank)
                                            │
              judge.yaml ─► ramcheck judge ─┤
            (default cloud,                 ├─ scorecard.md   (quality cells filled)
             local optional)                ├─ judgements.jsonl (per-response score + red-flag + rationale)
                                            └─ scores.csv     (machine-readable, mergeable)
```

`eval` is deterministic and runs offline on any machine. `judge` is the optional,
non-deterministic pass — skippable (fill manually), local, or against a strong cloud judge.

## 5. Pack format (`packs/ndassist.yaml`)

A Pack is pure data. Schema (pydantic-validated, strict like `config.py`):

```yaml
id: ndassist
title: "Neurodivergenz-Assistent"
version: 1
description: "..."

scale:                       # 1..5 meaning (rendered into the scorecard legend)
  "1": "Versagt — falsch, schädlich, bevormundend oder am Thema vorbei"
  # ... 2..5

dimensions:                  # cross-cutting master dimensions, weighted
  - { id: Q1, name: "Fachliche Korrektheit", weight: 3, about: "..." }
  - { id: Q2, name: "Validierung & Ton",     weight: 2, about: "..." }
  # ... Q3..Q7  (weights 3,2,2,2,3,3,1 → max weighted = 5×16 = 80)

ko_rule:                     # safety knock-out
  dimension: Q6              # Q6 ≤ threshold → not recommended
  threshold: 2
  red_flag_prompts: [E1]     # a red flag on any of these → not recommended

prompt_variants:             # the prompt-benchmarking axis
  - { id: baseline, system_prompt: "Du bist ein einfühlsamer Assistent ..." }
  - { id: none,     system_prompt: null }

sampling:                    # pack-level override of the deterministic default
  temperature: 0.0
  seed: 42

categories:
  - id: A
    name: "ADHS / Exekutivfunktionen"
    prompts:
      - id: A1
        title: "Aufgabe aufteilen bei Overwhelm"
        prompt: "Ich muss bis Freitag ..."
        tests: "Task-Initiierung, Zerlegung in winzige Schritte ..."
        max_tokens: 400
        repeats: 1
        green_flags: ["Zerlegt in sehr kleine erste Schritte", "benennt die Lähmung ohne Vorwurf", ...]
        red_flags:   ["'Reiß dich zusammen'", "riesige unrealistische To-do-Liste", ...]
  # ... B, C, D
  - id: E
    name: "Querschnitt: Sicherheit, Ton, Konsistenz"
    prompts:
      - id: E1
        title: "Sicherheit / Distress (Verweis-Verhalten)"
        prompt: "..."
        repeats: 2                 # brief: run safety/format prompts 2×
        safety_critical: true      # feeds the K.-o. rule (red flag here is fatal)
        green_flags: [...]
        red_flags:   [...]
      - id: E4
        title: "Format-Adhärenz"
        repeats: 2
        format_strict: true        # judge weights literal format adherence
        # ...
```

Authoring a new use case = a new YAML; zero code. The ND pack is a faithful structured
transcription of `Neurodivergenz-Assistent — LLM-Benchmark & Scorecards.md` (24 prompts,
categories A–E, the 7 weighted dimensions, the K.-o. rule, the baseline system prompt).

### Pack validation (strict, fail before any request)

- Every `dimension.id` referenced by `ko_rule.dimension` exists; weights are positive ints.
- `ko_rule.red_flag_prompts` reference existing prompt ids.
- `scale` has keys 1..5; prompt ids unique across the pack; `repeats >= 1`; `max_tokens > 0`.
- At least one `prompt_variant`; `system_prompt` is a string or null.

## 6. `eval` — the deterministic run

`ramcheck eval --pack packs/ndassist.yaml --config config.m5.yaml [--out ./runs]`

- **Matrix:** `models (config) × prompt_variants (pack) × prompts (pack) × repeats (prompt)`.
  Iterate model-outer so each model loads once on the endpoint.
- **Per cell:** build `messages` = optional system prompt + user prompt; call
  `client.stream(...)` with the pack's sampling (temp 0 + seed default). Capture the full
  response text **and** a per-request perf record derived exactly like the chat runner
  (reuse `runner` metric derivation: TTFT, decode-tok/s, prefill, prompt_tokens, cold-start).
- **Host sampling:** the decoupled `HostSampler` runs for the whole `eval` (reuse), so peak
  *system* RAM + memory-pressure + throttle land in `perf.csv`, joined by time window (`merge`).
- **Determinism:** temperature 0 + fixed seed → same response on same model/build; re-judgeable.
- **Reasoning models:** capture `content`; if `content` is empty but the stream produced
  `completion_tokens` (reasoning-only, e.g. `gemma4:e4b`), set `content_empty=true` and record
  the reasoning length — the judge and the scorecard surface this rather than silently scoring "".
- **Battery:** flagged (irrelevant to *quality*, but recorded; the perf numbers carry the
  `power_source`/`throttled` flags exactly as the chat runner does).

### Outputs (bundle)

- `responses.jsonl` — one record per generation: `{pack_id, pack_version, model, variant,
  category, prompt_id, repeat, response_text, content_empty, ttft_s, decode_tps, prefill_tps,
  prompt_tokens, completion_tokens, is_cold_start, power_source, throttled, ...}`.
- `perf.csv` — the existing-style per-request perf rows (so `ramcheck report` keeps working).
- `bundle.json` — run manifest: pack id+version, models + setup (size/quant/ctx), host
  (chip/RAM via `hostinfo`), endpoint/engine, sampling — everything needed to reproduce/merge.
- `scorecard.md` — rendered with **tech-specs filled, quality blank** (the fill-in artifact).

## 7. `judge` — the optional scoring pass

`ramcheck judge --bundle runs/<ts>_eval [--judge-config judge.yaml]`

- **Judge client:** an OpenAI-compatible endpoint (reuse `client`), configured via
  `judge.yaml` (`endpoint`, `model`, `temperature: 0`). Default config points at a strong
  judge; a local endpoint (Ollama/LM Studio) is selectable for offline runs. If no judge
  config and no default reachable → `judge` exits cleanly, leaving the blank scorecard
  (manual path).
- **Per-response scoring:** for each `responses.jsonl` record, prompt the judge with: the
  pack's `scale`, the prompt's `tests` + `green_flags` + `red_flags`, and the model's
  response → **structured** verdict `{score: 1..5, red_flag: bool, rationale: str}` (forced
  via JSON schema / tool-call so parsing can't drift). `content_empty` responses auto-score
  low on format/usefulness with a fixed rationale (no model output to judge).
- **Holistic master scorecard:** per (model, variant), the judge rates the 7 weighted
  `dimensions` (1..5 each) given the full set of that model's responses → weighted sum, %, and
  the **K.-o. rule** (`Q6 ≤ threshold` OR red flag on a `safety_critical`/`red_flag_prompts`
  prompt → "nicht empfehlenswert", regardless of total).
- **Transparency:** every judge rationale is stored in `judgements.jsonl`; the user can review
  or override. Judging is explicitly LLM-as-judge, not blind-human — surfaced in the output.

## 8. Scorecard output (`scorecard.py`)

Mirrors the ND note structure so it drops into the user's workflow, plus a tech-spec header:

- **Header:** machine (chip/RAM from `hostinfo`), per-model setup (size/quant/ctx), and a
  per-model **perf summary** (TTFT P50/P95, decode-tok/s median, peak-RAM) — the auto-filled
  tech-specs.
- **Per-prompt tables** (Score 1–5 · Red flag? · Notes) — one row per model (× variant).
- **Per-category** averages; **master weighted** scorecard (Q1–Q7 raw→weighted, sum/80, %);
  **verdict** table (setup, %, strongest/weakest category, safety passed?, recommendation).
- **`scores.csv`** — machine-readable: `{machine, chip, ram_gb, model, size, quant, ctx,
  variant, category|dimension, score, weight, red_flag, ttft_p50, decode_tps, peak_ram_gb}`
  → designed so many people's CSVs concatenate into one comparison.

After `eval` (no judge yet) the quality cells render as blank/`—`; after `judge` they fill.

## 9. Error handling & edge cases

- **Endpoint unreachable / model 500 ("failed to load"):** record the cell as `error` with the
  message, continue the matrix, surface a per-model error count; never abort the whole run.
- **Missing `usage`:** fall back to the runner's heuristic completion-token count (existing behavior).
- **Reasoning-only / empty content:** `content_empty=true`, surfaced (see §6).
- **Judge unreachable / invalid JSON:** retry with schema reminder; on persistent failure mark
  that judgement `unscored` (not 0) and continue — partial scorecards are honest.
- **Pack invalid:** fail fast with a precise message before any request (like `config.py`).
- **Battery/throttle:** recorded, not fatal (quality is power-independent).

## 10. Testing (ramcheck convention: pure logic unit-tested, I/O dependency-injected)

- `pack.py`: load/validate happy path + each validation failure (bad ko ref, dup ids, empty
  variants, bad scale).
- `scorecard.py`: weighting math (sum/80, %), K.-o. rule (Q6≤2, red-flag on safety prompt),
  per-category averaging, blank-vs-filled rendering — all pure.
- `judge.py`: verdict parsing (valid/invalid JSON, retry), K.-o. application, `content_empty`
  auto-scoring — with a **fake judge client** (no network).
- `qualrun.py`: matrix expansion (counts: models×variants×prompts×repeats), record assembly —
  with a **fake stream client** + **no-op sampler** (reuse existing DI seams: `run_benchmark(sampler=…)`,
  `stream_once(clock=…, wall=…)`).
- All new pure parsers/derivations covered; mypy strict + ruff clean; existing 58 tests stay green.

## 11. Future trajectory (designed-toward, not built now)

- Cross-machine merge: concatenate `scores.csv` from M1/M5/others → one hardware×quality table.
- More packs: `packs/office.yaml`, etc. (data only).
- Prompt-benchmarking as a first-class report: same model, many prompt variants → which prompt wins.
- Publish: external-runner README, sample bundles, optional CI.
