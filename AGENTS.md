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
hostinfo.py host metadata for the report header (macOS version, chip, RAM) — degrades
            to "unknown" off-mac so reports still render elsewhere
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
result_schema.py canonical per-(model×variant) result.json (ResultDoc, pure) — SSOT for
            the report renderer, GUI compare view, and cross-machine aggregator
aggregate.py cross-run/machine: many scores.csv → one Hardware×Quality table (md + scores_all.csv)
toolbench.py deterministic tools pack (Coding-Agent): opencode tool schemas as data, streamed
            tool calls (client.stream_tools) → checks (schema, complete calls, empty arguments,
            code vs asserts, edit applies) — no judge; `tools` + `tools-compare` (McNemar)

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
- **Die TTFT-Aggregate messen auf einer Engine mit Prefix-Cache den Cache, nicht die Latenz — maßgeblich ist die Spalte „TTFT kalt".** Der Latenz-Runner schickt je Zelle `runs_per_cell`-mal **denselben** Prompt; ab dem zweiten entfällt auf LM Studio, vLLM und Splash fast der ganze Prefill (Splash: P50 0,21 s gegen 7,3 s kalt). Die ehrliche Latenz ist der **erste** Request der Zelle — Cold-Start für die erste Zelle, sonst der ohnehin verworfene Warmup; `report._first_requests` leitet ihn aus den vorhandenen Flags ab (bewusst kein eigenes Feld, das dieselbe Wahrheit doppelt hielte). In der ersten Zelle trägt er zusätzlich das Laden des Modells und ist mit ⁽ᴸ⁾ markiert — gefunden erst an echten Daten (`mlx_lm.server`, cacht nicht: kalt 6,42 s gegen 0,32 s in Zelle 0, 3,85 gegen 4,17 s in Zelle 1). `cached_tokens` (aus `usage.prompt_tokens_details`, in `RunRecord`/`raw.csv` direkt hinter `actual_prompt_tokens`) belegt Cache-Treffer, statt sie zu vermuten; `None` heißt „Engine meldet es nicht" und ist **nicht** 0 — `mlx_lm.server` und Ollama melden es nicht, der Report zeigt dann `—`. Alte Bundles (alles bisher gegen `mlx_lm.server`/Ollama) sind nachweislich nicht betroffen: kalt ≈ warm ab Zelle 1. Nebenbei behoben: `report.load_raw_csv` erkannte optionale Zahlen an einer handgepflegten Namensliste, der `sys_used_delta_mb` fehlte — das Modell-Delta kam als **String** zurück; jetzt entscheidet der deklarierte Typ. `tests/test_schema_doc.py` hält die Spaltenliste in `docs/reference/metrics-and-schema.md` gegen `RAW_CSV_COLUMNS` (sie war schon einmal um `sys_used_delta_mb` hinterher).
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
- **Reasoning-Effort als `extra_body`, oberste Body-Ebene.** `ModelSpec.extra_body = {"reasoning_effort": "medium"}`
  landet über das OpenAI-SDK **auf oberster Ebene** im Request (wie opencodes `options.reasoningEffort`);
  LM Studio liest es dort, `chat_template_kwargs` reicht es **nicht** durch. Das qwen3.8-Template kennt nur
  `xhigh` (Default) / `medium` / `low` und wirft sonst einen Template-Fehler → `tools` hat einen **Pre-Flight**
  mit exakt diesem `extra_body` und bricht vor der Matrix ab (Eval: `--strict-preflight`). Das Label (`quant`)
  muss den Effort tragen (`4bit-medium`), sonst kollidieren gleich quantisierte Bundles im Vergleich; das
  Manifest hält `extra_body`/`reasoning_effort` fest (`tests/test_reasoning_effort.py`, echte CLI gegen Stand-in).
- **Remote-Endpoint (z. B. Hetzner Inference) = nur Qualität.** Key per `endpoint.api_key_file` (nie ins YAML; eine solche Config nicht als `config*.yaml` ins Repo-Root legen — `tests/test_config.py` validiert jede ausgelieferte Config und scheiterte ohne Token-Datei). TTFT/tok/s/RAM eines Remote-Bundles sind ohne Aussage (der Sampler misst den lokalen Mac). Hetzner (vLLM hinter Gateway „HeRay"): Reasoning in `delta.reasoning`, `reasoning_effort` oben im Body wirkt (`none` = Aus; `max` gibt es nur dort, lokal 400 → nicht in Vergleichsmatrizen), SSE-Zeilen `data:{…}` ohne Leerzeichen (SDK ok, Handparser nicht), `prompt + max_tokens ≤ 262144` sonst 400.
- **Eval-Resume nimmt die Modelle aus dem Bundle**, nicht aus der Config: vorher lief ein mit `--models-json`
  gestartetes Bundle beim `--resume` mit den `models:` der Config weiter (still ein anderes Modell).
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
- **`temperature 0` ist bei Thinking-Modellen gegen die Hersteller-Empfehlung — und ein Verdacht bei Endlos-Reasoning.** Die Configs fahren `temperature 0.0` + `seed` für Vergleichbarkeit. Qwen sagt für den Thinking-Modus ausdrücklich das Gegenteil: **kein Greedy-Decoding**, es führe zu „performance degradation and endless repetitions" (empfohlen dort `temperature 1.0` / `top_p 0.95` / `top_k 20` / `min_p 0` / `presence_penalty 0`; Instruct/Non-Thinking `0.7` / `0.80` / `20` / `presence_penalty 1.5`, bei quantisierten Modellen ausdrücklich 1.5 gegen Wiederholungen — Modellkarte `Qwen/Qwen3.8-27B` + Qwen-Quickstart, abgerufen 2026-09-20). Im Nachtlauf 2026-09-19 lief `qwen3.8-27b@4bit` bei `xhigh` **zweimal mit 103.164 Zeichen Reasoning** ins 32k-Budget, ohne sichtbaren Content — genau das beschriebene Muster. **Folge für die Auswertung:** Ein Effort-Vergleich unter `temperature 0` bleibt intern fair (alle Stufen gleich), überzeichnet aber vermutlich den Nachteil der hohen Stufen; wer die Zahlen weitergibt, nennt das. **Ungelöst** ist der Zielkonflikt Determinismus ↔ Hersteller-Empfehlung (Cockpit-Task „Sampling-Politik für Thinking-Modelle entscheiden"); ein `seed` allein macht einen Lauf bei `temperature > 0` nicht reproduzierbar, solange die Engine keine Seed-Treue zusagt.
- **LM Studio (MLX) ignoriert `presence_penalty` / `frequency_penalty` / `repetition_penalty` still — und seine Defaults sind immer das Thinking-Profil.** Gemessen von der Session llm-setup (App 0.4.24+1, MLX 1.11.0, 2026-09-20): Bei flacher Verteilung blieb die Ausgabe mit `presence_penalty 2.0`/`frequency_penalty 2.0`/`repetition_penalty 1.5` **zeichengleich**; nur LM Studios eigenes Feld `repeat_penalty` wirkt (1.1 ändert die Folge). Ohne Sampling-Felder gilt die Hub-`model.yaml` (`temperature 1.0`/`top_k 20`/`top_p 0.95`) — **auch bei `reasoning_effort: none`**, es gibt keinen Modus-Wechsel. Wer also ein „Instruct-Profil" mit `presence_penalty 1.5` über `extra_body` schickt, bekommt es lokal nicht; gegen vLLM (Hetzner) schon. Für den Harness heißt das: Ein lokal gemessenes Bundle und ein Remote-Bundle unterscheiden sich in den Penalties **nicht** (beide faktisch ohne), und ein Sampling-Profil (Cockpit-Task) muss `repeat_penalty` als LM-Studio-Sonderweg vorsehen.
- **Die `@quant`-Schreibweise schützt eine Messung NICHT — jeder JIT-Load eines anderen Clients entlädt das Modell.** Ursache ist LM Studios Einstellung `unloadPreviousJITModelOnLoad: true`: Ein Load verdrängt das zuvor per JIT geladene Modell samt Prompt-Cache. Konkret gemessen von der Session opencode (2026-09-20, 09:29:56–09:30:49 im LM-Studio-Log): **Schon der Start einer opencode-Session reicht**, weil deren `small_model` (`google/gemma-4-e2b`) einen Titel-Aufruf schickt → gemma lädt → `qwen@4bit` wird entladen → der nächste qwen-Request bricht den gemma-Load ab (`Operation canceled`) → qwen lädt neu. Für den Harness heißt das: **Während eines Messlaufs keine opencode-Session starten** (auch nicht „nur kurz"), und `Model unloaded.` / `Operation canceled` in `responses.jsonl` hat damit zwei bekannte Ursachen — echte Speicherkonkurrenz und diese Verdrängung. Die Treiber-Skripte behandeln beide gleich (Zeile verwerfen, `--resume`), die *Deutung* ist aber verschieden: Verdrängung ist ein Bedienfehler, kein Speicherproblem.
- **Zwei Hashes, zwei Fragen: `pack_fingerprint` (Resume) und `checks_fingerprint` (Vergleich).** Der strenge `pack_fingerprint` (ganze YAML + Kontextdateien) bewacht `--resume`: Ein Parameterwechsel mitten in einem Bundle würde Zellen unter verschiedenen Bedingungen mischen. Der Vergleich fragt etwas anderes — „wurden dieselben **Checks** gemessen" — und benutzt `checks_fingerprint` (Items, Checks, Fixtures, Tool-Schemas, Rahmen-Prompts, Token- **und** Zugbudget; **ohne** `sampling`). Grund: Zwei Effort-Stufen, jede mit ihrem vom Hersteller empfohlenen Sampling (Thinking `1.0`/`0.95` vs. Instruct `0.7`/`0.80`), sind verschiedene Läufe **desselben** Prüfstands; der frühere Ein-Hash-Wächter verweigerte genau diesen Vergleich (gemessen 2026-09-20 gegen Hetzner). `tools-compare` **benennt** den Sampling-Unterschied jetzt als Warnzeile im Bericht, statt abzubrechen — ein Teil des Unterschieds ist dann Sampling und nicht Denkstufe, und das muss der Leser sehen. Alte Bundles ohne `checks_sha256` fallen auf den strengen Hash zurück. `bundle.json` schreibt beide.
- **`finish_reason` gilt über alle Züge, `max_turns` ist 5 — beides aus dem ersten v3-Lauf gelernt.** Der Mehrzug-Lauf endet mit einem Abschlusszug **ohne** Aufruf, also `finish_reason: stop`. Ein Item, das `tool_calls` verlangt (M1, L1), fiel deshalb durch, obwohl es die Aufgabe erfüllt hatte (gemessen 2026-09-20 gegen Hetzner). Der Check prüft jetzt **irgendeinen** Zug (`ToolTurn.finishes` trägt die Liste; `finish_reason` bleibt der des letzten Zugs, also das Ende der Episode). Und `max_turns` steht auf **5**, nicht 3: Eine echte opencode-Sitzung brauchte für eine vergleichbare Aufgabe 4–5 Schritte (von der opencode-Session gemessen: `build` 4, ihre alte Thinking-Konfiguration 5), mit 3 wurden die mehrstufigen Items **mitten in der Erkundung** abgeschnitten (M3 verbrauchte alle drei Züge mit `read`/`bash`). Preis: Jeder Zug schickt den ganzen Verlauf erneut, das Budget kostet also Prefill — bei den Langkontext-Items (L*) ist das der dominierende Posten.
- **Die Schleife reicht das Reasoning des vorigen Zugs zurück — unter BEIDEN Feldnamen, nicht nur `reasoning_content`.** Von der opencode-Session am Draht gemessen (2026-09-20, opencode 1.18.31): Die assistant-Nachricht des zweiten Zugs trägt `content`, `reasoning_content`, `role`, `tool_calls`; opencode liest **beide** Lieferformen (`delta.reasoning` wie Hetzner, `delta.reasoning_content` wie LM Studio) und schickt immer `reasoning_content` zurück. Die Gegenstelle liest es auch: gegen Hetzner 668 statt 343 Prompt-Token, und das Modell kannte ein Codewort, das **nur** im Reasoning stand. `follow_up_messages` bildete das zunächst nur mit `reasoning_content` nach — bis eine Cross-Session-Messung (2026-09-21) gegen einen anderen Remote-Endpoint zeigte, dass dessen Tool-Chain **ausschließlich** `reasoning` liest und mit nur `reasoning_content` jede Kette nach genau zwei Zügen still abbricht (`finish_reason stop`, leerer Content, HTTP 200 — sieht wie Modellversagen aus, ist aber unser Serialisierungs-Bug). Seit Commit `fade3f6` schickt `follow_up_messages` deshalb **beide** Feldnamen gleichzeitig in derselben assistant-Nachricht — kostet ein paar Bytes, kostet aber nie eine Kette. Ohne das Echo würde ein Thinking-Modell bei uns ohnehin ohne Erinnerung an seinen eigenen vorigen Schritt weiterarbeiten, also etwas anderes als der Agent, den wir messen wollen. (Nebenbefund von der opencode-Session: `opencode run --format json` gibt **kein** `reasoning`-Ereignis aus, auch lokal nicht — eine Lücke der Ausgabe, kein Verwerfen. Ungeprüft bleibt, ob Reasoning früherer Runden nach einer neuen Nutzer-Nachricht erhalten bleibt; Qwens Template verwirft es dort üblicherweise mit Absicht.)
- **`temperature`/`seed` werden bei `None` wirklich weggelassen, nicht als 0.0/42 gesendet (`ToolsSampling`, Commit `b97255b`, 2026-09-22).** Bis dahin schickte `stream_tools()` beide Felder immer mit, egal was der Pack-YAML trug — ein Client, der sie nie setzt (z. B. opencode ohne `temperature: true` am Modell-Eintrag, verwirft den Wert dann ersatzlos), ließ sich damit nicht replizieren; jede Messung „wie der echte Client es schickt" maß in Wahrheit die eigene 0.0/42-Vorgabe. Jetzt `float | None` / `int | None`: `None` lässt das Feld im Request-Body ganz weg. Cross-Session-Messung (2026-09-22, gpt-oss-Modell hinter einem Harmony-übersetzenden Proxy) bestätigte den Unterschied handfest — mit expliziten 0.0/42 blieb `completion_tokens` über 20 Wiederholungen exakt konstant, ohne beide Felder schwankte es spürbar; die Auslassung greift also wirklich. **Nebenbefund derselben Messreihe** (235 Direkt-Requests, 0 Kontextlecks à la „Klartext-JSON statt Tool-Call" — über vier Sampling-Varianten, 1–10 Tool-Schemas und Kontextgrößen bis ~19k Token, alles einzeln und kombiniert): Ein extern über einen Coding-Agent beobachteter, nicht-deterministischer Fehlermodus (~15–32 % der Läufe) ließ sich am nackten Endpunkt mit keiner dieser drei Variablen reproduzieren — Sampling, Werkzeugmenge und Kontextlänge sind damit als alleinige Ursache ausgeschlossen. Offene, unbelegte Hypothese: eine Harmony→OpenAI-SSE-Übersetzung (Proxy-Schicht) legt die Tool-Call-Argumente unter bestimmten, noch unbekannten Bedingungen in den Content-Kanal statt in `delta.tool_calls` — nicht weiter verfolgt, keine Repo-Aufgabe hier (der betroffene Endpoint ist projektfremd, Details dazu bewusst nicht in diesem AGPL-Repo).
- **Gegen die Verdrängung schützt `lms load` — aber nur mit derselben Schreibweise, sonst liegt das Modell zweimal im Speicher.** Von der Session llm-setup gemessen (2026-09-20, Messreihe in deren `docs/reference/setup.md`): **Jeder** JIT-Load entlädt das zuvor per JIT geladene Modell, unabhängig von der Größe (auch das 4-GB-`gemma-4-e2b` verdrängte die 16 GB); die nackte ID und `@4bit` sind zwei Identifier **ohne** gemeinsame Instanz und verdrängen sich in beiden Richtungen; zwei JIT-Instanzen entstehen nie. Per `lms load` geladene Modelle sind davon **ausgenommen** (LM-Studio-Doku: „Non-JIT loaded models are not affected"). Kosten einer Verdrängung: der Prompt-Cache ist weg — derselbe 7.524-Token-Prompt kalt 42,2 s, warm 0,9 s, nach **einem** fremden Zwischen-Request wieder 39,9 s. **Die Falle:** `lms load` nimmt nur den **nackten** Schlüssel (mit `@` scheitert es), und wer danach `@4bit` anfragt, bekommt eine **zweite** Instanz desselben Artefakts — gemessen 2 × 16,08 GB. Wer eine Messung also gegen fremde JIT-Loads immunisieren will, muss Laden **und** Anfragen auf dieselbe Schreibweise stellen (nackter Schlüssel + `lms load`), sonst verdoppelt er den Speicher und zerstört genau das Modell-Delta, das er messen wollte ([[multimodel-ram-confound]] in der Projekt-Memory). Mit `@4bit` über JIT bleibt die Messung schnell und sauber, aber verdrängbar — dann gilt weiter: während eines Laufs kein anderer Client.
- **Das Tools-Pack wertet die Summe aller Züge, nicht den ersten (Pack v3, 2026-09-20).** v2 wertete
  nur den ersten Zug und bestrafte damit Umsicht: Gegen Hetzner waren **alle 11 Durchfaller** dasselbe
  Muster — das Modell rief erst `ls` / `find` / `read` / `mkdir -p` und erst danach die Zielaktion, die
  in opencodes Schleife folgen würde (`none` tat das nie → 24/24, xhigh 20/24; der Unterschied war die
  Metrik, nicht das Modell). v3 fährt eine **Agenten-Schleife**: Auf jeden Aufruf bekommt das Modell ein
  gestelltes Tool-Ergebnis (`tool_result_for`: `read` auf eine Fixture → die echte Datei im
  opencode-Format, `read` auf Unbekanntes → „not found", sonst die im Item hinterlegte Antwort
  (`turn_results`, Muster → Ausgabe, Daten wie alles andere) oder die neutrale Quittung `(exit 0)` —
  **nie erfundene Dateiinhalte**) und darf weitermachen, bis es keine Aufrufe mehr macht oder
  `pack.max_turns` (3) erschöpft ist. Die Schleife endet **nicht**, wenn die Checks erfüllt sind — das
  wäre Wertung im Lauf. `merge_turns` führt die Züge zu dem einen Turn zusammen, gegen den die Checks
  laufen (Aufrufe aneinander, `prompt_tokens` vom letzten Zug, Dauern summiert, Fehler des letzten);
  `n_turns` + `turn_calls` stehen in `responses.jsonl`, CSV und Bericht, denn **jeder Zug kostet in
  opencode einen weiteren Request**. Mit der Schleife wurde `arg_equals`/`arg_regex` von „der **erste**
  Aufruf des Tools" auf „**irgendein** Aufruf" umgestellt; ein *verbotenes* Muster (`absent: true`)
  gilt spiegelbildlich für **jeden** Aufruf, sonst entschuldigte ein harmloses `ls` das verbotene Flag
  daneben. Exakte `calls`-Zahlen auf `bash` sind zu `min` geworden (Erkundung darf einen Aufruf kosten);
  auf `write`/`read` bleiben sie exakt (Zielaktion bzw. „nicht mit Schrot schießen"). **v2- und
  v3-Bundles sind nicht vergleichbar** — `compare_bundles` verweigert gemischte `pack_version`, Resume
  ohnehin (Pack-Hash). Alle Hetzner-tools-Zahlen aus v2 sind damit Altlast und gehören nicht in einen
  Bericht.
- **Das Tools-Pack (`touchstone tools`, `packs/opencode-tools.yaml`) wird nie gejudged.** Strukturierter
  Output ist mechanisch prüfbar; ein Judge brächte dort nur Rauschen. Die Tool-Schemas sind **Daten im
  Pack**, aus dem opencode-Binary 1.18.31 übernommen (bash dort `{command, timeout?, workdir?}`, *kein*
  `description`); das read-Ausgabeformat (`N: text`) ebenso — Edit-Items bekommen die Fixture als
  vorangegangenes read-Ergebnis. `calls` zählt nur **vollständige** Calls (arguments parsen zu einem
  Objekt): LM Studio liefert einen am Budget abgeschnittenen Call mit Namen, aber leerem `arguments` aus
  (der „Missing key at [content]"-Fall) — der darf das Soll nicht füllen. Code-Checks führen
  **unbeaufsichtigt Modell-Code** aus: `sandbox-exec` (kein Netz, Schreiben nur im Temp-Dir), leeres
  Env, kein stdin, eigene Prozessgruppe (SIGKILL nach 20 s); Python-Code wird per `runpy` als Modul
  **nicht** `__main__` geladen (ein Demo-`__main__`-Block darf nicht laufen). Braucht das Pack `node`
  und fehlt es, bricht `tools` vorab ab — sonst bestünden JS-Items ungeprüft. **Transportfehler sind
  kein Modellbefund:** nach 3 in Folge bricht der Lauf ab (`ToolsAborted`), `--resume` wiederholt die
  Fehler-Zellen (letzte Zeile je Zelle gilt). **Der Exit-Code trägt das mit:** `tools` endet mit **1**,
  sobald auch nur *eine* Fehler-Zelle offen ist — nicht erst beim Abbruch. Verstreute Fehler (unter 3
  in Folge) liefen vorher mit rc=0 durch, und die Schlusszeile zählte sie als „nicht bestanden", also
  wie ein gemessenes Scheitern; ein Treiberskript konnte „Modell fiel durch" nicht von „nie gemessen"
  unterscheiden (CORE-TEST-19). Die Quote zählt jetzt nur **gemessene** Zellen, die offenen stehen als
  eigene rote Zeile mit `--resume`-Hinweis daneben. `tools-compare` wertet Fehler-Paare nicht und lehnt
  Bundles mit mehr als einem Modell ab; Resume verlangt denselben Pack-Hash (inkl. Kontext). Unbekannte
  Argument-Keys werden **gezählt, nicht bestraft** (opencode ignoriert sie vermutlich); Edits prüfen
  **exakt** — strenger als opencodes 9 Fallback-Strategien, ein Beinahe-Treffer steht im Detail. Jede Prüfung ist in `tests/test_toolbench.py` gegen eine
  Referenzantwort (muss bestehen) **und** eine kaputte (muss scheitern) abgesichert — wer ein Item
  ändert, pflegt beide mit.
  Die Langkontext-Items **L1–L3** sind per YAML-Anker identisch mit M1/E3/C1, bekommen aber vorher ~50k
  Token Vorkontext (12 Module als read-Ergebnisse aus `packs/opencode-tools-context/`, ein **eingefrorener**
  Schnappschuss als `.py.txt` — nie nachziehen, sonst sind Läufe untereinander unvergleichbar). Obergrenze:
  8bit-JIT lädt mit 131072 Kontext, Vorkontext + 32k Budget muss darunter bleiben (Test prüft die Größe).
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
- **Kein GitHub-Mirror mehr (2026-09-05).** Der `github`-Remote ist entfernt. GitHub war als
  Backup-Gegenstelle gedacht; diese Rolle trägt seit 2026-07 `git.jkaindl.de`, und seit dem
  2026-08-30 ist das Konto ohnehin geflaggt (anonym 404, Actions kontoweit aus). Bleiben soll
  GitHub nur, wo es funktional erzwungen ist — die Obsidian-Store-Kette; dieses Repo ist kein
  Plugin. Der Absatz darunter bleibt als **Begründung** stehen, warum ein Mirror, solange es
  ihn gab, von Hand gepusht werden musste.
- ⚠️ **Beide Remotes explizit pushen — der Mirror trägt nicht.** Bis 2026-07-28 stand hier, ein
  Forgejo→GitHub-Push-Mirror (`sync_on_commit`) ziehe GitHub automatisch nach. Gemessen war das
  falsch: GitHub hing **11 Commits** zurück (die gesamte `docs/decisions/`-Ebene *und* Release
  `0.2.0`), und ein `origin`-Push zog auch danach nicht nach (zweimal geprüft, sofort und nach
  20 s). Also immer:

  ```bash
  git push origin main && git push github main
  # Die Forges selbst messen, nicht die lokalen Tracking-Refs (die koennen stale sein):
  git rev-parse main
  git ls-remote --heads origin main
  git ls-remote --heads github main      # drei identische Hashes = fertig
  ```

  (`git rev-parse --short a b c` bricht mit „Needed a single revision" ab — `--short` nimmt nur
  **eine** Revision. Ohne `--short` funktioniert die Mehrfach-Form, misst aber eben nur die
  lokalen Kopien.)

  Die Zusage bleibt hier als Warnung stehen, statt gelöscht zu werden: sie hat elf Commits lang
  verdeckt, dass die öffentliche Seite veraltet war. Wird der Mirror je repariert, gehört das
  **an den Remotes gemessen**, nicht in dieser Datei behauptet.
- Auth: Forgejo-Token `~/.forgejo-token`, GitHub-Token `~/.github-token` (HTTPS, nicht in `.git/config`).

## Abweichungen von der Leitkonvention

- **CORE-META-03/04** — Doku-Einstieg `docs/README.md` (Hero/Landing + Diátaxis-Karte), die
  Entscheidungs-Ebene `docs/decisions/` (ADRs + Index, je Kontext/Alternativen/Auswirkungen),
  `docs/reference/` + `docs/explanation/` (Narrativ, verlinkt die ADRs) sowie seit **2026-07-28**
  `docs/tutorial.md` + `docs/how-to/` (4 Guides + Index) sind erstellt. Die READMEs (de/en)
  erfüllen den `readme-spec.json`-Tier `python-cli` — `readme_lint.py --strict` ist grün.
  **Noch offen:** Hero-*Bild*.
