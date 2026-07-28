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
cli.py      typer app: run [--web] · embed · report · aggregate · eval [--web] · judge

# qualitative use-case evaluation (the second half — answer quality, not speed):
pack.py     a use-case "pack" (YAML) → validated Pack: prompts + green/red flags +
            weighted dimensions + K.-o. rule + system-prompt variants. Data, not code.
results.py  eval data contract: EvalResponse (one answer + perf) · Verdict · ModelReport
qualrun.py  deterministic run: matrix (model × variant × prompt × repeat) → bundle
            (responses.jsonl + perf.csv), reusing stream_once + sampler + merge
runqueue.py overnight daisy-chain: `queue` command drives many models sequentially as
            isolated subprocesses (reset→settle→eval→[judge] per entry); pure logic + DI spawn
judge.py    pluggable LLM-as-judge (JudgeBackend protocol): per-answer score vs. flags
            + holistic weighted master scorecard + safety K.-o.
scorecard.py weighting/K.-o./category math (pure) + renders scorecard.md + scores.csv
aggregate.py cross-run/machine: many scores.csv → one Hardware×Quality table (md + scores_all.csv)

# live monitoring (Ink. 3/5 — opt-in `eval --web` / `judge --web`, a separate viewing process):
webmon.py   transport-only live-monitor process (stdlib http.server + SSE): tails the event
            file + (eval only) resources.jsonl, serves a read-only dashboard via a pluggable
            VIEW module chosen by --view eval|judge. Spawned like _SamplerProcess.
events.py   eval view: events.jsonl contract (append-only) + build_view + INDEX_HTML;
            TAILS_RESOURCES=True (the eval dashboard shows live host load)
judge_events.py  judge view: judge_events.jsonl contract + build_view (score histogram,
            red-flags, ETA, master-scorecard preview) + INDEX_HTML; TAILS_RESOURCES=False
tail.py     byte-offset tail tolerant of missing/partial files (used by webmon)
loadview.py latest host-load snapshot from resources.jsonl (used by the eval view)
```

The qualitative half is **two decoupled phases**: `eval` (deterministic, on the
machine under test — fills tech-specs, leaves quality blank) and `judge` (optional,
non-deterministic — fills quality from the captured answers). A use-case is a **pack**
(`packs/*.yaml`); a new use case is a new YAML, no code. Two packs ship:
`packs/ndassist.yaml` (Neurodivergenz-Assistent, 24 prompts, safety K.-o.) and
`packs/buero.yaml` (Büro-/Wissensarbeit-Assistent, 25 prompts, K.-o. = no-hallucination
on faktische Zuverlässigkeit). Both phases are **incremental +
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

uv run pytest -q                               # tests (no server/sudo needed)
uv run ruff check . && uv run ruff format .    # lint + format
uv run mypy touchstone/                          # strict type-check
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
- **System-Peak vs Modell-Delta — the cross-machine number is the delta.** The raw `sys_used_mb`
  peak ("System-Peak") includes the whole OS + every other process, so it is **not** comparable
  across machines. We report `sys_used_delta_mb = peak − baseline` ("Modell-Delta") as the
  comparable figure. **Baseline** = the `baseline=True` resources.jsonl tick captured at
  `sampler.run_to_file` *before the first request* (or, on an older bundle without the marker,
  `min` of all samples). The harness drives an *already-running* endpoint: on a pre-loaded server
  the delta is the inference-time growth (KV/context/activations); on a lazy-loading server it
  also includes the weights. The delta lives in unified memory where the context/KV share is **not**
  cleanly separable from the weights (documented imprecision); per-prompt KV isolation is out of
  scope. `merge.resources_for_window` derives the baseline from the *full* sample list (the baseline
  tick is outside every run window) and threads it into `aggregate_window`; `RunRecord` /
  `EvalResponse` / `scores.csv` (`model_delta_gb`) all carry it. **When you add a RAM field, keep
  the `RAW_CSV_COLUMNS ↔ RunRecord` import-time assertion green** (`sys_used_delta_mb` sits right
  after `sys_used_mb` in both).
- **Cold-start** is the very first request of a whole run (flagged `is_cold_start`), reported
  on its own line, never in the aggregates.
- **Reasoning ("thinking") models are captured, not dropped.** `client.py` reads the separate
  `delta.reasoning_content` / `delta.reasoning` field into `StreamEvent.reasoning_text`; `stream_once`
  accumulates it (separately from content, so it never triggers content-TTFT) and `run_eval` records
  its length as `EvalResponse.reasoning_chars`. A reasoning-only answer (e.g. ollama `gemma4:e4b`) has
  `content_empty=True` with a non-zero `reasoning_chars`; the monitor shows a 💭 marker per such cell.
  `stream_once` also records `reasoning_duration_s` / `reasoning_tps` (heuristic token count) and the
  first-reasoning-token time, surfaced per-answer and as a "Thinking" compare row.
- **Prefill/decode rates start at the first *generated* token, not the first content token.** For a
  reasoning model the reasoning stream begins long before the first content token, so `derive_rates`
  would otherwise shrink the decode window to ~0 and explode `decode_tps` (and starve `prefill_tps`).
  It uses `min(ttft, t_reasoning_start)` as the prefill/decode boundary. `ttft_s` itself stays "time to
  first *visible content*" — the UX latency metric, deliberately distinct from the throughput boundary.
- **Reasoning-only is scored `unscored`, not a silent 1/5.** `judge.py:score_response` splits the
  empty-content path on `reasoning_chars`: reasoning-only (content empty *and* reasoning present) →
  `unscored` (excluded from the mean, `REASONING_ONLY_RATIONALE`) — it is *our* token-starvation, not a
  verdict on the model; a genuinely empty answer (no reasoning either) still scores 1 (`EMPTY_RATIONALE`).
  `EvalResponse.reasoning_text` is persisted **only when `content_empty`** (keeps `responses.jsonl` slim)
  so the thinking stays inspectable, and `scorecard.reasoning_only_counts` surfaces the per-(model,variant)
  count in the tech-specs table.
- **Judge-Runaway-Guard.** Der Judge-Call ist gebunden — `OpenAIJudgeBackend` nimmt `timeout`
  (`JudgeConfig.call_timeout_s`, Default 120 s) + `max_retries=0` + optionalen `max_tokens` und wirft
  `JudgeCallError` statt ~30 min zu hängen. `judge_responses` degradiert eine gescheiterte Zelle zu
  einem `unscored`+`judge_error`-Verdict (⚠-Begründung) — **nicht** an `on_verdict` gestreamt und im
  Clean-Rewrite **nicht** persistiert, damit ein Resume sie neu bewertet — und bricht nach
  `max_consecutive_failures` (Default 3) mit `JudgeAborted` ab (der CLI fängt das → rote Meldung +
  Exit 1, Sentinel `failed`). Grund: **Thinking-Modelle als Judge drehen durch** (kein Cap, Thinking
  nie aus) → nimm ein dense Modell wie `qwen3.6-27b` ([[judge-thinking-model-runaway]]). `score_dimensions`
  fängt den Fehler ebenfalls (degradierter Report). CLI **und** GUI erben den Guard (die GUI spawnt den
  CLI-Subprozess); die einzige Backend-Konstruktion ist `cli.py`. Der Backend-`except` reicht
  Nicht-`OpenAIError` (Programmierfehler) durch, statt sie als Judge-Fehler zu maskieren. **Bekannte
  Grenze (selten):** schlägt *nur* der holistische Master-Call fehl (Per-Antwort-Pass war ok), wird ein
  degradierter `_error`-Report mit `dim_scores={}` geschrieben und der Lauf endet sauber (Exit 0) — die
  Nacht-Queue sieht `reports.jsonl` und re-judged ihn nicht automatisch; ein manuelles `judge` heilt ihn.
- **Pre-flight smoke runs before the matrix.** `preflight.preflight_models` sends one small request per
  model with that model's *effective* budget (`max(pack prompt max_tokens) + ModelSpec.reasoning_headroom_tokens`)
  after `iter_eval_cells` but **before** `sampler.start()` (never inside the measured window) and classifies
  `ok | reasoning_only | empty | error`. It **never raises** and never measures. Default **warns** (CLI print
  + a `preflight` event in `events.jsonl` → folded by `events.build_view` → GUI live banner / webmon banner);
  `eval --strict-preflight` raises before the matrix (no run-dir garbage). `resume` skips it.
- **Eval lets models answer freely by default.** `PackPrompt.max_tokens` defaults to `None` (no cap) for the
  qualitative eval — closer to real use, and a reasoning model isn't starved before it reaches visible content.
  `client.stream` omits `max_tokens` from the API call when `None` (the server decides, context-window-bounded);
  a positive int caps it (set per pack prompt for a controlled budget). The **latency runner** (`run`) keeps its
  fixed `config.max_tokens_for` budget — it measures decode under a comparable load.
- **Thinking knobs on `ModelSpec` are opt-in and default-neutral.** `reasoning_headroom_tokens` is a fine-tuning
  knob: it adds extra *total* budget for THAT model **on top of an explicit `pack.prompt.max_tokens` cap** (the
  visible answer budget stays the cap → fair compare); with the default free budget there's no cap to add to, so
  it's ignored. A small cap is a legitimate test setup, so the harness never inflates it silently. `extra_body`
  is forwarded verbatim to `chat.completions.create` (e.g. `{"chat_template_kwargs": {"enable_thinking": false}}`)
  — engine-agnostic, no engine branch outside `client.py`, forwarded only when non-empty. The judge backend
  never disables thinking.
- **Judge model is pickable like the eval model.** `gui/configs.discover_models` is the shared discovery
  core; `discover_judge_endpoint_models` + `GET /judge-endpoint-models` (never-500, `judge*.yaml` path-guard)
  feed a GUI dropdown; `judge --judge-model <id>` overrides the YAML model for one run (no write-back),
  mirroring eval's `--models-json`.
- **The live monitor never tails `responses.jsonl`.** It is rewritten wholesale at finalize
  (`qualrun._write_responses`). Live progress comes from the append-only `events.jsonl`
  (fed by `run_eval`'s `on_cell_*` callbacks); live host-load from `resources.jsonl`.
- **The judge monitor (`judge --web`) never tails `resources.jsonl`.** The judge runs on a
  *different* endpoint (a cloud/local judge), so the bundle's `resources.jsonl` (from the
  earlier `eval` run) is stale and irrelevant. The judge view (`touchstone/judge_events.py`,
  `TAILS_RESOURCES=False`) shows score distribution / red-flags / a master-scorecard preview
  instead of a load panel. The master `%`/safety values are computed in the host process and
  shipped pre-rendered (the monitor never sees the `Pack`). Like `eval --web`, the judge path
  is byte-identical without `--web` (additive callbacks, default off) and `_hold_monitor`
  keeps the dashboard up until Ctrl-C.
- **The GUI (`touchstone gui`) is an optional `[gui]` extra and never measures in-process.** The
  long-lived FastAPI server (`touchstone/gui/`, deps isolated in the `[gui]` extra — the core never
  imports it) is an **out-of-process control-plane**: it spawns `python -m touchstone eval/judge`
  with `--run-dir <host-chosen>` `--emit-events` (event-writers without the webmon monitor — the GUI
  tails `events.jsonl` itself) and reuses the pure read layer (`load_pack`, `scorecard.master_rows`,
  `aggregate`, `tail`+`build_view`). `runs/` stays the SSOT; the GUI's only state is a **transient
  run-sentinel** (`run.json` in the active run dir) that triples as run_dir handle, cross-process
  **one-run lock** (survives a GUI restart; only one measurement at a time = mess-cleanliness), and
  discovery anchor for running/crashed runs. Sentinel + event files are transient, **excluded from
  discovery and the `/export` allowlist**. Status + verdict are **derived/recomputed** per bundle
  (not in `bundle.json`). `serve()` binds 127.0.0.1 only; TrustedHost + Origin checks guard the
  state-changing POSTs against DNS-rebinding/CSRF.
- **`reports.jsonl` is the `ModelReport` persistence** (one line per (model, variant): `dim_scores`
  + `dim_rationales`), written by `judge` in BOTH paths at finalize (`_render_judge_scorecard`). It is
  the **sole** source of per-dimension rationales (`scores.csv` has none); the GUI reads it first and
  falls back to the lossy `scores.csv` reconstruction (then „Begründung nicht erfasst"). Master
  dimensions are scored **holistically** (one judge call over all answers) — so traceability runs
  through the judge's rationale **citing prompt_ids** (clickable in the result view), not through a
  dimension→prompt structure (none exists). The evaluation method is explained in
  `docs/explanation/design-decisions.md` (+ ADR `docs/decisions/0007-holistische-dimensionen.md`) and
  surfaced UI-retrievably via `templates/_method_explainer.html`.
- **`resources.jsonl` ticks carry `cpu_pct`** (system CPU %, `None` for pre-cpu ticks) — additive,
  does not touch `RAW_CSV_COLUMNS`. The result view plots RAM + CPU over the run.
- **GUI-Modell-Override (ephemer):** Die „Konfig + Start"-Seite zeigt die `models:` der gewählten
  Config als Checkboxen + eine Ad-hoc-Zeile (id/quant). Die Auswahl geht als `models_json` an
  `/runs/eval` → `RunRegistry.start_eval(models=…)` → `eval --models-json` → `apply_models_override`
  **ersetzt** `config.models` nur für diesen Lauf (Endpoint/seed bleiben aus der Config; nichts wird
  in die Config zurückgeschrieben — das Bundle protokolliert, was lief). Leeres/ungültiges
  `models_json` → 400, kein Spawn; `resume` ignoriert den Override. Pure Logik:
  `config.models_from_json`/`apply_models_override`, `gui/configs.py`.
- **Endpoint-Modell-Discovery:** Der Picker fragt beim Config-Wechsel `GET /endpoint-models?config=…`
  ab; der Server ruft `/v1/models` des Config-Endpoints (`OpenAIStreamClient.list_models`, Timeout 3 s
  **+ `max_retries=0`** → ~3 s harter Bound, sonst dehnen SDK-Retries einen toten Endpoint auf ~10 s)
  und liefert `{"models": [...], "error": null}` — **nie 500**, ein toter Endpoint ergibt `error` +
  leere Liste. Die Route lässt nur die angebotenen `config*.yaml` zu (kein Lesen beliebiger cwd-YAML).
  Das Dropdown füllt sich daraus; „Hinzufügen" legt eine Auswahl-Zeile an (dedupt per `id` gegen schon
  Gewähltes). Default-Config ordnet `*embed*`/`*vlm*` nach hinten (`order_configs`); der Picker-Default
  kommt aus der geordneten `configs`-Liste (`configs[0]`), nicht aus der JSON-Key-Reihenfolge.
  Pure Logik: `configs.discover_endpoint_models`. **`OpenAIStreamClient`-Timeout/`max_retries` nur
  forwarden, wenn gesetzt** — sonst überschriebe `timeout=None` den 600s-SDK-Default für alle eval/judge-Läufe.
- **Zwei Vergleichs-Ebenen, klar getrennt:** `/compare` (Station 6) ist das **Cross-Run-Aggregat**
  über *alle* Bundles (`aggregate.load_all_scores` → Tabelle Hardware×Qualität). `/compare/{bundle}`
  ist der **Innerhalb-Bundle-Achsen-Vergleich** (Ink. 8): eine kontrollierte Ansicht entlang `model`
  oder `variant` *innerhalb eines* Bundles — Effizienz-Scatter (x=Decode, y=Qualität, r=Peak-RAM
  `sys_used_mb`), Kopf-an-Kopf (`master_rows`⨝`reports`-Join; `master_rows` trägt kein `dim_scores`),
  per-Aufgabe-Drill-down (per-Prompt `Verdict.score`-Δ, V7 — orthogonal zur holistischen %).
  CPU wird GUI-seitig aus `resources.jsonl`-Fenstern berechnet (`compare._cpu_for_window`); da
  `cpu_pct` erst mit Ink. 7 kam und kein Bundle seither neu lief, ist CPU heute überall **„n. v."**
  (ein first-class getesteter Zustand). Pure Logik in `touchstone/gui/compare.py`.
- **Meta-Report (`/export-meta-report`) muss `render_report_md`s judging=0-Strip selbst re-applizieren.**
  `render_report_md` nullt verdicts/reports/master_rows **zentral** bei `include_judging=False` (ein Ort).
  Die daraus extrahierten `report_md.section_*`-Helfer sind aber **nicht** judging-blind —
  `section_prompts_antworten` rendert `· Judge n/5` + `**Judge:**`-Badge, sobald ein Verdict vorliegt.
  `gui/meta_report.py` reicht daher unter judging=0 `verdicts=[]` durch (`_bundle_detail_section`) und ruft
  `_eval_task` statt `section_master_scorecard`; sonst leaken Judge-Scores in den „Bewertungs-Auftrag" und
  verankern die Re-Judge-Cloud-KI (Sub-Projekt F). **Wer einen weiteren Composer über die `section_*`-Helfer
  baut, muss denselben Strip re-applizieren.** Die Bytegleichheit der Zerlegung sichert ein Golden-Test
  (`tests/test_report_md_golden.py`); `pool_rows` folgt **keinen** Symlinks → ein extern hineingelinktes
  Bundle erscheint nicht im `/compare`-Pool (Confinement sicher-by-construction, `is_relative_to`-Guard =
  Defense-in-Depth).
- **Die Nacht-Queue (`touchstone queue`) erkennt Fertigstellung am Subprozess-Exit (+ Finalize-Artefakten),
  nie an einem Fortschrittsbalken.** Ein eval/judge-Subprozess existiert erst *nach* `_finalize`, also gibt
  es kein „100 % ≠ fertig"-Problem (die holistische Judge-Phase liefert ~0 Events, läuft aber weiter — genau
  dieser Fall lähmte einen Live-Monitor). Hänger fängt der **per-Step-Watchdog** (`step_timeout_s`:
  SIGTERM→SIGKILL). Zwischen Einträgen läuft `reset_command` (Default `lms unload --all`) + ein
  konditionsbasiertes RAM-**Settle**, damit jedes Modell eine frische Baseline bekommt → sauberes
  Modell-Delta (ein Modell pro Eintrag, [[multimodel-ram-confound]]). Der Reset ist ein **konfigurierbares
  Shell-Kommando** (Daten, kein Engine-Branch); `--check` verifiziert die LM-Studio-JIT-Load+Eviction-Kette
  vor dem ersten echten Lauf. `load_queue` lehnt doppelte `(config, pack, model)`-Einträge ab (sonst
  stilles Bundle-Überschreiben). `runqueue.py` ist pure Logik + DI (Spawn/RAM/Clock) → ohne Server/sudo
  getestet; der dünne `queue`-Command verdrahtet `subprocess`/`psutil`/`time`.

## Memory

Projekt-Memory under `~/.claude/projects/-Users-Shared-code-llm-benchmark-harness/memory/`
(index: `MEMORY.md`). Session-Handoff under `.remember/` (gitignored). Vault cockpit:
`10_Pallas/25_Coding/llm-benchmark-harness/` (status/tasks/decisions).

## Hosting

- **`origin`** = Codeberg (primär): <https://codeberg.org/jkaindl/llm-benchmark-harness>
- **`github`** = GitHub (Mirror): <https://github.com/johannes-kaindl/llm-benchmark-harness>
- ⚠️ **Beide Remotes explizit pushen — der Mirror trägt nicht.** Bis 2026-07-28 stand hier, ein
  Codeberg→GitHub-Push-Mirror (`sync_on_commit`) ziehe GitHub automatisch nach. Gemessen war das
  falsch: GitHub hing **11 Commits** zurück (die gesamte `docs/decisions/`-Ebene *und* Release
  `0.2.0`), und ein `origin`-Push zog auch danach nicht nach (zweimal geprüft, sofort und nach
  20 s). Also immer:

  ```bash
  git push origin main && git push github main
  git rev-parse --short main origin/main github/main   # drei identische Hashes = fertig
  ```

  Die Zusage bleibt hier als Warnung stehen, statt gelöscht zu werden: sie hat elf Commits lang
  verdeckt, dass die öffentliche Seite veraltet war. Wird der Mirror je repariert, gehört das
  **an den Remotes gemessen**, nicht in dieser Datei behauptet.
- Auth: Codeberg-Token `~/.codeberg-token`, GitHub-Token `~/.github-token` (HTTPS, nicht in `.git/config`).

## Abweichungen von der Leitkonvention

- **CORE-META-03/04** — Doku-Einstieg `docs/README.md` (Hero/Landing + Diátaxis-Karte), die
  Entscheidungs-Ebene `docs/decisions/` (ADRs + Index, je Kontext/Alternativen/Auswirkungen),
  `docs/reference/` + `docs/explanation/` (Narrativ, verlinkt die ADRs) sowie seit **2026-07-28**
  `docs/tutorial.md` + `docs/how-to/` (4 Guides + Index) sind erstellt. Die READMEs (de/en)
  erfüllen den `readme-spec.json`-Tier `python-cli` — `readme_lint.py --strict` ist grün.
  **Noch offen:** Hero-*Bild*.
