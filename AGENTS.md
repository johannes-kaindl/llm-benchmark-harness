# AGENTS.md

Conventions for AI agents (Claude Code, Codex, …) working on this repository.

## Project character

`llm-ramcheck` is a **thin** benchmark harness for *local* LLM endpoints. It drives a
fixed prompt set through an **OpenAI-compatible** endpoint, records TTFT / prefill-tok/s
/ decode-tok/s **with their distribution**, and — in a **decoupled process** — samples
macOS memory pressure and thermal throttling, then merges both into one Markdown table
(+ CSV). The whole point: the *same code* runs on an M1 (LM Studio, `:1234`) and an M5
(mlx_lm.server `:8080` / mlx-openai-server `:8000`); **only the config changes**.

Non-negotiable design decisions (from the architect brief):
- **OpenAI-compatible is the only interface.** No engine-specific code in the hot path
  (the single thin adapter is `client.py`).
- **Two decoupled producers, merged by timestamp:** latency runner + host sampler.
  Memory is **never** estimated from the request thread.
- **Output is Markdown + CSV, nothing else.** No DOCX/PDF/HTML.
- **Distribution over mean:** TTFT P50/P95, decode/prefill median, TTFT CV% for consistency.
- Warmup discarded, **cold-start TTFT reported separately**; throttled/battery runs flagged
  and excluded from aggregates (kept raw).

## Architecture principles

Data flows through one shared contract (`models.py`); keep modules from drifting apart.

```
config.py   YAML → validated Config (pydantic)
prompts.py  scenario builders + deterministic token padding to 4K/16K/32K
runner.py   stream_once() metric derivation · iter_cells() · run_benchmark() orchestration
client.py   the ONLY engine-aware code — adapts the OpenAI SDK stream to StreamEvent
sampler.py  decoupled host sampler (psutil + powermetrics + memory_pressure + pmset)
merge.py    latency log × resource log, joined by [t_start,t_end] window per run
stats.py    P50/P95 · median · CV%   (pure, no numpy)
report.py   raw.csv (every request) + report.md (SSOT columns, aggregates exclude noise)
embed.py    embedding throughput sub-run
cli.py      typer app: run · embed · report · eval · judge

# qualitative use-case evaluation (the second half — answer quality, not speed):
pack.py     a use-case "pack" (YAML) → validated Pack: prompts + green/red flags +
            weighted dimensions + K.-o. rule + system-prompt variants. Data, not code.
results.py  eval data contract: EvalResponse (one answer + perf) · Verdict · ModelReport
qualrun.py  deterministic run: matrix (model × variant × prompt × repeat) → bundle
            (responses.jsonl + perf.csv), reusing stream_once + sampler + merge
judge.py    pluggable LLM-as-judge (JudgeBackend protocol): per-answer score vs. flags
            + holistic weighted master scorecard + safety K.-o.
scorecard.py weighting/K.-o./category math (pure) + renders scorecard.md + scores.csv
```

The qualitative half is **two decoupled phases**: `eval` (deterministic, on the
machine under test — fills tech-specs, leaves quality blank) and `judge` (optional,
non-deterministic — fills quality from the captured answers). A use-case is a **pack**
(`packs/*.yaml`); a new use case is a new YAML, no code. `packs/ndassist.yaml` is the
first one (Neurodivergenz-Assistent, 24 prompts). Both phases are **incremental +
resumable**: answers/verdicts are appended as they finish, so an interrupted run is
continued with `eval --resume <bundle>` (or just re-running `judge`) — done cells are
skipped, a half-written final line is tolerated.

- `models.RAW_CSV_COLUMNS` is the **single source of truth** for the CSV schema and is
  asserted against `RunRecord` at import — change one, change both.
- Pure logic (stats, padding, merge, metric derivation, host-tool parsers) is unit-tested;
  I/O (runner stream, sampler loop) is dependency-injected so it stays testable without a
  live server or sudo (`stream_once(clock=…, wall=…)`, `run_benchmark(sampler=…)`).
- Don't add engine-specific branches outside `client.py`. Don't drive Obsidian/Smart
  Composer/Local GPT — the **endpoint** is measured, not the UX layer.

## Commands

```bash
uv sync                                        # venv + deps (+ dev group)
uv run ramcheck run    --config config.m1.yaml # M1 → LM Studio
uv run ramcheck run    --config config.m5.yaml # M5 → mlx_lm.server / mlx-openai-server
uv run ramcheck embed  --config config.m5.yaml # embedding throughput
uv run ramcheck report --runs ./runs           # (re)generate report.md from raw.csv

uv run ramcheck eval   --pack packs/ndassist.yaml --config config.m5.yaml  # qualitative run → bundle (tech-specs auto)
uv run ramcheck eval   --pack packs/ndassist.yaml --config config.m5.yaml --resume runs/<ts>_eval_ndassist  # nach Abbruch weiter
uv run ramcheck judge  --bundle runs/<ts>_eval_ndassist --judge-config judge.yaml  # LLM-as-judge → filled scorecard (resumebar)

uv run pytest -q                               # tests (no server/sudo needed)
uv run ruff check . && uv run ruff format .    # lint + format
uv run mypy ramcheck/                          # strict type-check
uv run --extra tokenizer python -c "..."       # exact per-model tokenizer (transformers)
```

## Conventions

Workspace-wide standards live in `../_docs/CONVENTIONS.md` (profile **python-uv**). Project-specific:
- Python 3.12, `uv` only (committed `uv.lock`). Ruff (line-length 100) + mypy strict.
- German is fine in prompts, report output and commit descriptions; code/identifiers English.
- `runs/` is gitignored — `report.md` is pasted by hand into the SSOT test note.
- The shipped configs (`config.*.yaml`) must always validate (`tests/test_config.py` guards it).

## Gotchas

- **Throttle detection needs sudo.** `sampler.py` spawns `sudo -n powermetrics`. Without
  passwordless sudo for it, the throttle flag stays `False` and runs are **not** excluded
  as throttled (they're just unflagged). Either grant NOPASSWD for `powermetrics` or accept
  that throttling won't be detected on that machine.
- **`engine_version` is often `unknown`.** The OpenAI-compatible API doesn't expose
  mlx_lm/mlx/LM-Studio build versions. Set `engine` / `engine_version` in the config to get
  them into the report header.
- **`usage` may be missing** even with `stream_options.include_usage`. The runner then
  falls back to a heuristic completion-token count; prefer servers that emit usage.
- **VLM needs a vision model.** `prompts/vlm_sample.png` is a text-bearing document page
  (swap in your own for representative load), but the `vlm` scenario only works against a
  **vision-capable** model on the endpoint — a text-only model rejects the image content.
- **Peak-RAM = system memory, not RSS.** On Apple Silicon mlx mmaps the weights into unified
  memory, so the server-PID RSS undercounts the model (~0.2 GB vs ~18 GB). The report leads with
  peak *system* memory + `memory_pressure`; RSS is only a parenthetical hint.
- **Cold-start** is the very first request of a whole run (flagged `is_cold_start`), reported
  on its own line, never in the aggregates.

## Memory

Projekt-Memory under `~/.claude/projects/-Users-Shared-code-llm-benchmark-harness/memory/`
(index: `MEMORY.md`). Session-Handoff under `.remember/` (gitignored). Vault cockpit:
`10_Pallas/25_Coding/llm-benchmark-harness/` (status/tasks/decisions).

## Hosting

- **`origin`** = Codeberg (primär): <https://codeberg.org/jkaindl/llm-benchmark-harness>
- **`github`** = GitHub (Mirror): <https://github.com/johannes-kaindl/llm-benchmark-harness>
- Codeberg→GitHub **Push-Mirror** ist aktiv (`sync_on_commit`): ein Push auf `origin` spiegelt
  automatisch nach GitHub. Direkt auf `github` zu pushen ist daher i. d. R. unnötig.
- Auth: Codeberg-Token `~/.codeberg-token`, GitHub-Token `~/.github-token` (HTTPS, nicht in `.git/config`).

## Abweichungen von der Leitkonvention

- **CORE-META-03/04** — Hero-Bild + volle Diátaxis-Doku noch nicht erstellt (Reife: Alpha;
  `docs/reference/` + `docs/explanation/` als Start vorhanden).
