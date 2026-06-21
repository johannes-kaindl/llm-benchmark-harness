# llm-ramcheck

> 🇬🇧 English · [🇩🇪 Deutsch](README.de.md)

A thin CLI that benchmarks a local LLM through an **OpenAI-compatible endpoint** —
recording TTFT, prefill-tok/s and decode-tok/s **with their distribution** while a
**decoupled host sampler** tracks macOS memory pressure and thermal throttling, then
merges both into one Markdown table (+ CSV). It also evaluates **answer quality**
(deterministic capture + optional LLM-as-judge) and can drive the whole loop from a local
**web control-center**. Same code on an M1 (LM Studio) and an M5 (mlx-lm) — you only swap
the config.

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Docs: CC BY-SA 4.0](https://img.shields.io/badge/docs-CC%20BY--SA%204.0-lightgrey.svg)](LICENSE-DOCS)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)

## Quick Start

```bash
uv sync
# Latency benchmark — M1 → LM Studio (:1234), M5 → mlx_lm.server (:8080):
uv run ramcheck run    --config config.m1.yaml
uv run ramcheck embed  --config config.m5.yaml   # embedding throughput (separate)
uv run ramcheck report --runs ./runs             # (re)build report.md from raw.csv

# Quality evaluation — run a use-case pack, then score it:
uv run ramcheck eval   --pack packs/ndassist.yaml --config config.m5.yaml
uv run ramcheck judge  --bundle runs/<ts>_eval_ndassist --judge-config judge.yaml
uv run ramcheck aggregate --runs ./runs          # cross-machine Hardware×Quality table

# Web control-center (optional [gui] extra):
uv sync --extra gui
uv run ramcheck gui                              # configure → start → watch → evaluate → compare → export
```

## Usage

Point `endpoint.base_url` at your local server, set `machine`/`models`, and run.
Each run sweeps a scenario × context-bucket matrix, discards one warmup per cell,
measures a separate cold-start TTFT, and writes a `report.md` (whose columns paste
straight into a comparison note) plus a `raw.csv` with one row per request.

Two processes stay decoupled by design: the **latency runner** (requests) and the
**host sampler** (`psutil` + `powermetrics` + `memory_pressure` + `pmset`), joined
afterwards by timestamp. Memory is never estimated from the request thread.

The harness reports **distribution, not the mean**: TTFT as P50/P95, decode/prefill
as median, and TTFT consistency as CV%. Throttled and on-battery runs are flagged and
excluded from the aggregates (kept raw in the CSV).

**Quality evaluation (the second half).** Performance doesn't say whether a model
*answers well*. `eval` runs a use-case **pack** (`packs/*.yaml` — prompts + green/red flags +
weighted dimensions + a safety knock-out + system-prompt variants) deterministically on the
machine under test, capturing answers and tech-specs; `judge` then scores them with a pluggable
LLM-as-judge into a weighted scorecard (`aggregate` rolls many bundles into one Hardware×Quality
table). Generation and judging are **two decoupled phases**, both incremental and resumable.
A new use case is a new YAML, not new code.

**Web control-center (`ramcheck gui`).** An optional local FastAPI server (the `[gui]` extra —
build-free HTMX/Alpine, isolated from the measurement core) puts the whole workflow in the
browser: see what each pack tests and how it's scored, configure and **start/stop** runs, watch
them live, browse results, compare across machines, and export. It is an **out-of-process
control-plane** — it spawns the same `ramcheck eval/judge` subprocesses the CLI does, so the
measurement loop stays decoupled and `runs/` remains the single source of truth. Bound to
127.0.0.1, one measurement at a time.

> **macOS note:** throttle detection shells out to `sudo powermetrics`. Allow
> passwordless sudo for it or the throttle flag stays off (runs are then *not*
> excluded as throttled). See [AGENTS.md](AGENTS.md) → Gotchas.

## Documentation

- [Reference](docs/reference/) — metric definitions, CSV schema, config keys
- [Explanation](docs/explanation/) — why distribution over mean, the two-process split, why the GUI is out-of-process

## License

Code: **AGPL-3.0** — see [`LICENSE`](LICENSE); commercial dual-license option in [`LICENSING.md`](LICENSING.md).
Documentation/text: **CC BY-SA 4.0** — see [`LICENSE-DOCS`](LICENSE-DOCS).
