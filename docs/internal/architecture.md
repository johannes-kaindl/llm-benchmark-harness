# Architecture — module map

Aus der `AGENTS.md` ausgelagert (CORE-AGENT-15): der Aufbau, den man nur beim Navigieren im Code braucht. Die Prinzipien stehen im Kern der `AGENTS.md`.

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
