# llm-ramcheck

> 🇬🇧 English · [🇩🇪 Deutsch](README.de.md)

A thin CLI that benchmarks a local LLM through an **OpenAI-compatible endpoint** —
recording TTFT, prefill-tok/s and decode-tok/s **with their distribution** while a
**decoupled host sampler** tracks macOS memory pressure and thermal throttling, then
merges both into one Markdown table (+ CSV). Same code on an M1 (LM Studio) and an
M5 (mlx-lm) — you only swap the config.

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Docs: CC BY-SA 4.0](https://img.shields.io/badge/docs-CC%20BY--SA%204.0-lightgrey.svg)](LICENSE-DOCS)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)

## Quick Start

```bash
uv sync
# M1 → LM Studio (http://localhost:1234/v1):
uv run ramcheck run    --config config.m1.yaml
# M5 → mlx_lm.server (:8080) / mlx-openai-server (:8000):
uv run ramcheck run    --config config.m5.yaml
# Embedding throughput (separate):
uv run ramcheck embed  --config config.m5.yaml
# (Re)generate report.md from collected raw.csv:
uv run ramcheck report --runs ./runs
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

> **macOS note:** throttle detection shells out to `sudo powermetrics`. Allow
> passwordless sudo for it or the throttle flag stays off (runs are then *not*
> excluded as throttled). See [AGENTS.md](AGENTS.md) → Gotchas.

## Documentation

- [Reference](docs/reference/) — metric definitions, CSV schema, config keys
- [Explanation](docs/explanation/) — why distribution over mean, the two-process split

## License

Code: **AGPL-3.0** — see [`LICENSE`](LICENSE); commercial dual-license option in [`LICENSING.md`](LICENSING.md).
Documentation/text: **CC BY-SA 4.0** — see [`LICENSE-DOCS`](LICENSE-DOCS).
