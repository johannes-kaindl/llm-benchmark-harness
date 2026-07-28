# llm-touchstone

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

## Features

- **Distribution, not the mean** — TTFT as P50/P95, decode/prefill as median, consistency as
  CV%. On Apple Silicon latency jumps at the memory threshold; an average hides exactly that.
- **Decoupled host sampler** — memory pressure, swap, power source and thermal throttling are
  recorded by a *separate process* and joined by timestamp, never estimated from the request
  thread.
- **Answer quality, not just speed** — a use-case **pack** (`packs/*.yaml`: prompts, green/red
  flags, weighted dimensions, a safety knock-out) runs deterministically; a pluggable
  LLM-as-judge turns it into a weighted scorecard. A new use case is a new YAML, not new code.
- **System prompts as a measured axis** — every prompt runs once per system-prompt variant, so
  the same run tells you whether your prompt actually helps.
- **Engine-agnostic by construction** — the only engine-aware file is `client.py`. LM Studio,
  mlx-lm, Ollama and llama.cpp are a config swap, never a code branch.
- **Resumable everywhere** — `eval`, `judge` and the overnight `queue` all continue where they
  stopped; nothing is lost to an interrupted run.
- **Cross-machine comparison** — `aggregate` rolls many bundles into one Hardware×Quality table,
  reporting the model delta (peak minus baseline) that actually compares across machines.
- **Overnight queue** — several models sequentially, each with a fresh RAM baseline
  (unload → settle → eval → judge), with per-step watchdogs.
- **Optional web control-center** — configure, start/stop, watch live, compare and export in the
  browser, as an out-of-process control-plane that never measures in its own process.

## Requirements

- **macOS.** The measurement core is portable, but the host sampler reads `memory_pressure`,
  `pmset` and `powermetrics`.
- **Python 3.12+** via [`uv`](https://docs.astral.sh/uv/) (`brew install uv`).
- **A local OpenAI-compatible endpoint** — LM Studio, `mlx_lm.server`, `mlx-openai-server`,
  Ollama or llama.cpp. If you don't have one yet, see
  [uplink.jkaindl.de/llm-setup](https://uplink.jkaindl.de/llm-setup).
- **Optional — passwordless sudo for `powermetrics`.** Without it the throttle flag stays off
  and throttled runs are *not* excluded (see
  [How-to: set up a new machine](docs/how-to/neue-maschine-einrichten.md)).
- **Optional — the `[gui]` extra** (FastAPI/uvicorn/Jinja) for the web control-center.

## Install

```bash
git clone https://codeberg.org/jkaindl/llm-benchmark-harness
cd llm-benchmark-harness
uv sync
uv sync --extra gui     # optional: web control-center
```

## Quick Start

```bash
uv sync
# Latency benchmark — M1 → LM Studio (:1234), M5 → mlx_lm.server (:8080):
uv run touchstone run    --config config.m1.yaml
uv run touchstone embed  --config config.m5.yaml   # embedding throughput (separate)
uv run touchstone report --runs ./runs             # (re)build report.md from raw.csv

# Quality evaluation — run a use-case pack, then score it:
uv run touchstone eval   --pack packs/ndassist.yaml --config config.m5.yaml
uv run touchstone judge  --bundle runs/<ts>_eval_ndassist --judge-config judge.yaml
uv run touchstone aggregate --runs ./runs          # cross-machine Hardware×Quality table

# Web control-center (optional [gui] extra):
uv sync --extra gui
uv run touchstone gui                              # configure → start → watch → evaluate → compare → export
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
A new use case is a new YAML, not new code. Two packs ship today: `ndassist`
(Neurodivergenz-Assistent) and `buero` (Büro-/Wissensarbeit-Assistent).

**Web control-center (`touchstone gui`).** An optional local FastAPI server (the `[gui]` extra —
build-free HTMX/Alpine, isolated from the measurement core) puts the whole workflow in the
browser: see what each pack tests and how it's scored, configure and **start/stop** runs, watch
them live, browse results, compare across machines, and export. It is an **out-of-process
control-plane** — it spawns the same `touchstone eval/judge` subprocesses the CLI does, so the
measurement loop stays decoupled and `runs/` remains the single source of truth. Bound to
127.0.0.1, one measurement at a time.

> **macOS note:** throttle detection shells out to `sudo powermetrics`. Allow
> passwordless sudo for it or the throttle flag stays off (runs are then *not*
> excluded as throttled). See [AGENTS.md](AGENTS.md) → Gotchas.

## Documentation

Docs are in German; the entry point is [`docs/README.md`](docs/README.md).

- [Tutorial](docs/tutorial.md) — one full pass: install → measure → evaluate → read the scorecard
- [How-to Guides](docs/how-to/) — [build your own pack](docs/how-to/eigenen-pack-bauen.md) ·
  [set up a new machine](docs/how-to/neue-maschine-einrichten.md) ·
  [run the overnight queue](docs/how-to/nacht-queue-fahren.md) ·
  [use the web control-center](docs/how-to/gui-nutzen.md)
- [Reference](docs/reference/metrics-and-schema.md) — metric definitions, CSV schema, config keys
- [Explanation](docs/explanation/design-decisions.md) — why distribution over mean, the two-process split, why the GUI is out-of-process
- [Decisions](docs/decisions/README.md) — 15 ADRs: context, alternatives, consequences

## License

Code: **AGPL-3.0** — see [`LICENSE`](LICENSE); commercial dual-license option in [`LICENSING.md`](LICENSING.md).
Documentation/text: **CC BY-SA 4.0** — see [`LICENSE-DOCS`](LICENSE-DOCS).
