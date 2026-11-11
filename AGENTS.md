# AGENTS.md

Conventions for AI agents (Claude Code, Codex, …) working on this repository.

## Project character

`llm-touchstone` is a **thin** benchmark harness for *local* LLM endpoints. It drives a
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
- **Persisted output is Markdown + CSV, nothing else.** No DOCX/PDF/HTML *artifacts*.
  (The opt-in `eval --web` live monitor serves HTML transiently for viewing; it persists
  nothing as HTML — the bundle stays jsonl/csv/md.)
- **Distribution over mean:** TTFT P50/P95, decode/prefill median, TTFT CV% for consistency.
- Warmup discarded, **cold-start TTFT reported separately**; throttled/battery runs flagged
  and excluded from aggregates (kept raw).

## Architecture principles

Data flows through one shared contract (`models.py`); keep modules from drifting apart.


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
uv run touchstone run    --config config.m1.yaml # M1 → LM Studio
uv run touchstone run    --config config.m5.yaml # M5 → mlx_lm.server / mlx-openai-server
uv run touchstone embed  --config config.m5.yaml # embedding throughput
uv run touchstone report --runs ./runs           # (re)generate report.md from raw.csv

uv run touchstone eval   --pack packs/ndassist.yaml --config config.m5.yaml  # qualitative run → bundle (tech-specs auto)
uv run touchstone eval   --pack packs/ndassist.yaml --config config.m5.yaml --resume runs/<ts>_eval_ndassist  # nach Abbruch weiter
uv run touchstone judge  --bundle runs/<ts>_eval_ndassist --judge-config judge.yaml  # LLM-as-judge → filled scorecard (resumebar)
uv run touchstone judge  --bundle runs/<ts>_eval_ndassist --judge-config judge.yaml --web  # + live judge monitor (score dist / red-flags / master preview)

uv sync --extra gui                            # install the optional web-UI deps (fastapi/uvicorn/jinja2)
uv run touchstone gui                            # local web control-center: configure→start→watch→evaluate→compare→export

uv run touchstone queue --queue queue.example.yaml          # Nacht-Queue: mehrere Modelle sequenziell eval→judge
uv run touchstone queue --queue queue.example.yaml --check  # nur die LM-Studio-Modell-Wechsel-Kette verifizieren (kein Matrix-Lauf)
uv run touchstone queue --queue queue.example.yaml --resume runs/<ts>_queue  # nach Abbruch weiter (fertige Einträge übersprungen)

uv run touchstone tools --pack packs/opencode-tools.yaml --config config.m5-lmstudio.yaml --models-json '[{"id":"<id>","quant":"<q>"}]'  # Tool-Calls/Code deterministisch
uv run touchstone tools-compare runs/<A> runs/<B> --out cmp.md   # Paarvergleich je Item + exakter McNemar

uv run pytest -q                               # tests (no server/sudo needed)
uv run ruff check . && uv run ruff format .    # lint + format
uv run mypy touchstone/                          # strict type-check
uv run --extra tokenizer python -c "..."       # exact per-model tokenizer (transformers)
```

## Conventions

> **Workspace standards (maintainer-local):** The binding Leitkonvention lives in `_docs/CONVENTIONS.md`
> (profile **python-uv**) in the maintainer's multi-project workspace, `../_docs` relative to this repo —
> not part of this repository, ignore if absent in your clone. Model: comply-or-explain.

Project-specific:
- Python 3.12, `uv` only (committed `uv.lock`). Ruff (line-length 100) + mypy strict.
- German is fine in prompts, report output and commit descriptions; code/identifiers English.
- `runs/` is gitignored — `report.md` is pasted by hand into the SSOT test note.
- The shipped configs (`config.*.yaml`) must always validate (`tests/test_config.py` guards it).
- Eine neue Gotcha gehört in die passende Regeldatei unter `.claude/rules/` (oder in eine neue mit `paths:`), nicht in diesen Kern; das Warum nach `docs/internal/decisions.md`, der Aufbau nach `docs/internal/architecture.md`. Der Kern bleibt unter 12.000 Einheiten (`kontext_lint` im `_docs`-Repo misst es).

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

## Context index

- Before you change `touchstone/sampler.py`, `merge.py`, `report.py`, `models.py` or `runner.py`, or add a RAM/CSV field: `.claude/rules/metrics-and-ram.md` (columns: `docs/reference/metrics-and-schema.md`).
- Before you change how `client.py` / `runner.py` handle reasoning, thinking or `finish_reason`: `.claude/rules/reasoning-capture.md`.
- Before you change `judge.py`, `scorecard.py`, the judge backend or the judge-model picker: `.claude/rules/judge-backend.md` (ADRs: `docs/decisions/0009-reasoning-only-unscored-preflight.md`, `docs/decisions/0010-judge-runaway-guard.md`).
- Before you change `preflight.py`, `qualrun.py`, `config.py`, `ModelSpec` knobs, `extra_body`, remote endpoints or eval resume: `.claude/rules/eval-run-and-endpoints.md`.
- Before you touch `webmon.py`, `events.py`, `judge_events.py`, `tail.py` or `loadview.py` (`eval --web` / `judge --web`): `.claude/rules/live-monitor.md`.
- Before you change anything under `touchstone/gui/` (control plane, compare, meta-report, `reports.jsonl`, model picker): `.claude/rules/gui.md` (method: `docs/explanation/design-decisions.md`, `docs/decisions/0007-holistische-dimensionen.md`, `docs/decisions/0014-gui-control-plane.md`).
- Before you change sampling, `config*.yaml`, `queue.example.yaml` or anything that talks to LM Studio (JIT loads, `lms load`, penalties): `.claude/rules/lmstudio-and-sampling.md`.
- Before you change `toolbench.py`, `packs/opencode-tools.yaml` or `packs/opencode-tools-context/`: `.claude/rules/tools-pack.md`.
- Before you change `runqueue.py` or the `queue` command: `.claude/rules/night-queue.md` (guide: `docs/how-to/nacht-queue-fahren.md`, ADR `docs/decisions/0015-nacht-queue.md`).
- To find a module or understand the qualitative/live-monitoring split: `docs/internal/architecture.md`. Why hosting and mirrors are as they are: `docs/internal/decisions.md`.


## Memory

Projekt-Memory under `~/.claude/projects/-Users-Shared-code-llm-benchmark-harness/memory/`
(index: `MEMORY.md`). Session-Handoff under `.remember/` (gitignored). Vault cockpit:
`$VAULT/25_Coding/llm-benchmark-harness/` (status/tasks/decisions; `$VAULT` = etablierter
CORE-META-14-Platzhalter für den Obsidian-Vault des Maintainers).

- **SDD-Artefakte (seit 2026-07-16): Cockpit, nicht Repo** — Specs/Plans/Task-Reports leben im
  Coding-Cockpit des Maintainers (`$VAULT/25_Coding/llm-benchmark-harness/_SDD/`, CORE-META-14,
  maintainer-lokal). Sie tragen Arbeitskontext (Vault-Pfade, Schwester-Repo-Interna), der in einem
  public Repo niemandem nützt. Das Repo behält die Design-Essenz in dieser Datei + `CHANGELOG.md`.
- **Alt-Bestand:** `docs/superpowers/{specs,plans}/` ist eingefroren — nichts Neues dort ablegen.
- **Nie im Repo:** absolute Pfade außerhalb des Repos (`/Users/…`, Vault-Pfade) — Platzhalter nutzen
  (`$VAULT/…`, `~/…`, repo-relativ). Herkunftsnachweise als Repo-Name + `Datei:Zeile` sind dagegen erwünscht.

## Hosting

- **`origin`** = git.jkaindl.de (primär): <https://git.jkaindl.de/jkaindl/llm-benchmark-harness>
- Auth: Forgejo-Token `~/.forgejo-token`, GitHub-Token `~/.github-token` (HTTPS, nicht in `.git/config`).

## Abweichungen von der Leitkonvention

- **CORE-META-03/04** — Doku-Einstieg `docs/README.md` (Hero/Landing + Diátaxis-Karte), die
  Entscheidungs-Ebene `docs/decisions/` (ADRs + Index, je Kontext/Alternativen/Auswirkungen),
  `docs/reference/` + `docs/explanation/` (Narrativ, verlinkt die ADRs) sowie seit **2026-07-28**
  `docs/tutorial.md` + `docs/how-to/` (4 Guides + Index) sind erstellt. Die READMEs (de/en)
  erfüllen den `readme-spec.json`-Tier `python-cli` — `readme_lint.py --strict` ist grün.
  **Noch offen:** Hero-*Bild*.
