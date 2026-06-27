# Spec: Nacht-Queue / Daisy-Chain (`touchstone queue`)

**Datum:** 2026-06-27 · **Status:** freigegeben (Johannes: „gerne autonom alles umsetzen") · **Projekt:** Nacht-Queue

## Problem

Mehrere Modelle sollen **über Nacht unbeaufsichtigt** sequenziell durchlaufen — pro Modell
ein vollständiger `eval → judge`-Zyklus, sodass am Morgen mehrere fertige Bundles in `runs/`
liegen. Heute ist das nur manuell (ein `eval`/`judge`-Aufruf nach dem anderen, von Hand)
machbar.

Ratifizierte Randbedingung ([[multimodel-ram-confound]]): **ein Modell pro Run** = frische
RAM-Baseline. Multi-Modell-pro-Run bläht das RAM-Delta ab Modell 2 auf (geteilte Baseline,
kein Unload). Die Queue ist also eine Kette **sequenzieller Einzel-Runs**, kein
Multi-Modell-Run.

Zwei Einsichten aus der Live-Diagnose des hängenden buero-Judge-Laufs am selben Tag fließen
direkt ein:
1. **„100 % ≠ fertig":** Die Fortschrittsanzeige (Per-Antwort-Verdicts) erreicht 100 %,
   während die **holistische Master-Scorecard-Phase** noch läuft und *keine* Events schreibt,
   bis sie finalisiert. Fertig-Erkennung darf nicht am Fortschrittsbalken hängen.
2. **Hänger-Risiko unbeaufsichtigt:** Ein Judge-Call, der nie zurückkommt (Endpoint tot /
   Modell evicted), sieht am Prozess identisch aus wie „langsam aber arbeitend" (0 % CPU im
   `recv`). Ohne Timeout versenkt ein toter Call die ganze Nacht.

## Nicht-Ziele (YAGNI)

- **Keine GUI-Integration.** Die GUI entdeckt fertige `runs/`-Bundles ohnehin automatisch —
  sie wird zum *Betrachter* der Nacht-Ergebnisse, ohne dass die Queue dort lebt. Ein
  langlebiger Webserver ist der falsche Träger für einen Fire-and-forget-Nachtlauf.
- **Keine Endpoint-CPU-basierte Hang-Erkennung** (fragil, Worker-PID-Identifikation
  LM-Studio-spezifisch) → v2.
- **Kein mlx_lm.server-Modell-Wechsel** (single-model → Server-Neustart pro Modell). Erste
  Version zielt auf **LM Studio :1234 JIT-Load**; der Reset-Hebel ist aber konfigurierbar
  (s. u.), sodass ein mlx-Pfad später nur Konfig ist.

## Architektur: Subprozess-Orchestrator (CLI treibt CLI)

`touchstone queue queue.yaml` ist ein **dünner, sequenzieller Orchestrator**, der pro Eintrag
`python -m touchstone eval …` und dann `python -m touchstone judge …` als **eigene
Subprozesse** spawnt — exakt das Muster, das die GUI-Control-Plane schon nutzt.

**Warum Subprozesse (statt in-process):** Maximale Isolation — ein OOM/Segfault/Hänger in
Eintrag 4 kann die Queue nicht mitreißen; 1–3 liegen fertig vor. Frischer Prozess pro
Eintrag = kein geleakter State, **saubere RAM-Baseline by construction**. Der Watchdog kann
einen toten Subprozess hart killen, ohne den Orchestrator zu gefährden. Fertig-Erkennung =
**Prozess-Exit 0 + Finalize-Artefakte da**, nie ein Fortschrittsbalken → das „100 % ≠ fertig"-
Problem entfällt strukturell (der Subprozess existiert erst nach `_finalize`).

## CLI-Oberfläche

```bash
touchstone queue queue.yaml                       # die Nacht durchlaufen
touchstone queue queue.yaml --check               # nur Modell-Wechsel-Kette verifizieren
touchstone queue queue.yaml --resume runs/<ts>_queue   # nach Abbruch fortsetzen
```

## `queue.yaml`-Schema

```yaml
defaults:                              # optional; jede Schlüssel pro Eintrag überschreibbar
  reset_command: "lms unload --all"    # zwischen Einträgen ausgeführt; "" = aus
  settle:
    timeout_s: 120                     # max Wartezeit aufs RAM-Plateau
    plateau_polls: 3                   # so viele aufeinanderfolgende stabile Polls = settled
    poll_interval_s: 2
    epsilon_mb: 200                    # |Δ sys_used| < epsilon = stabil
  step_timeout_s:
    eval: 14400                        # 4 h Watchdog
    judge: 21600                       # 6 h (judge ist langsam — ~4 h/Bundle real gemessen)
  cooldown_s: 0                        # optionale Extra-Pause nach settle, vor eval
entries:
  - config: config.m5-lmstudio.yaml
    pack:   packs/buero.yaml
    model:  { id: "google/gemma-4-12b" }          # genau EIN ModelSpec; String-Kurzform erlaubt
    judge_config: judge.yaml                       # weglassen → nur eval, kein judge
    judge_model:  "qwen/qwen3.6-27b"               # optional, überschreibt judge.yaml-Modell
  - config: config.m5-lmstudio.yaml
    pack:   packs/buero.yaml
    model:  { id: "qwen/qwen3.6-27b", reasoning_headroom_tokens: 2000 }
    judge_config: judge.yaml
```

- **`model`** ist ein voller `ModelSpec` (id Pflicht; `quant`/`reasoning_headroom_tokens`/
  `extra_body`/`max_tokens_default` optional, reisen mit). String-Kurzform `model: "id"` →
  `{id: "id"}`. Die Queue reicht ihn als `--models-json '[<ModelSpec>]'` an `eval` → die
  **Ein-Modell-pro-Run-Regel ist strukturell erzwungen** (genau ein Element). Konkret:
  Mapping → `ModelSpec(**mapping)` (String → `{id: …}`), validiert via pydantic, dann
  `json.dumps([spec.model_dump()])` als `--models-json`-Argument (Gegenstück zum bestehenden
  `config.models_from_json`).
- **`judge_config` weglassen** → der Eintrag macht nur `eval` (Bundle bleibt unbewertet,
  später per `judge` oder GUI nachholbar).
- **`defaults`** liefert globale Werte; ein Eintrag darf `reset_command`/`settle`/
  `step_timeout_s`/`cooldown_s` lokal überschreiben.

Validierung: pydantic-Modelle `QueueSpec` / `QueueEntry` / `QueueDefaults`. `config`/`pack`
müssen existieren; `judge_config` (falls gesetzt) muss existieren. `model` muss genau einen
gültigen `ModelSpec` ergeben (Reuse `config.models_from_json`-Bausteine).

## Per-Eintrag-Flow

Für jeden Eintrag i (in Reihenfolge):

1. **reset + settle** (vor *jedem* eval, auch Eintrag 1 → auch der bekommt eine saubere
   Baseline):
   - `reset_command` ausführen (Default `lms unload --all`). **Schlägt es fehl (non-zero /
     nicht gefunden) → warnen + weiter** (nie die Nacht abbrechen).
   - **settle-wait:** System-RAM (psutil) pollen, bis er **plateauet** (`|Δ| < epsilon_mb`
     für `plateau_polls` Runden) **oder** `timeout_s` erreicht. Konditionsbasiert, kein fixer
     Sleep. Bei Timeout: warnen + weiter (best effort).
   - optional `cooldown_s` Pause.
2. **eval-Subprozess:**
   `python -m touchstone eval --config <c> --pack <p> --models-json '[<ModelSpec>]'
   --run-dir runs/<ts>_<modelslug>_eval_<packid> --emit-events`
   - Die Queue **wählt `--run-dir` deterministisch** (Timestamp + Modell-Slug + Pack-Id) →
     kennt den Bundle-Pfad exakt, kein „neuestes raten".
   - `--emit-events` → `events.jsonl` wird geschrieben (GUI-/Webmon-kompatibel, ohne dass die
     Queue einen Monitor spawnt).
   - Watchdog: läuft der Prozess länger als `step_timeout_s.eval` → SIGTERM, nach Gnadenfrist
     SIGKILL.
3. **judge-Subprozess** (nur falls `judge_config`):
   `python -m touchstone judge --bundle <run-dir> --judge-config <jc>
   [--judge-model <jm>] --emit-events`
   - Watchdog analog `step_timeout_s.judge`.
4. **Eintrag protokollieren** (Status + Dauern + Bundle-Pfad) → inkrementell in `summary.json`
   geschrieben, *bevor* der nächste Eintrag startet (Crash-Robustheit + Resume-Basis).

**Fertig-Erkennung je Schritt:** Subprozess-Exit `0` **und** erwartete Finalize-Artefakte
vorhanden (eval: `responses.jsonl`; judge: `reports.jsonl` + `scores.csv`). Exit ≠ 0 oder
fehlende Artefakte → Schritt `failed`. Timeout-Kill → Schritt `timeout`.

## Fehler-Policy & Watchdog

- **continue-on-error:** Ein fehlgeschlagener/getimeouteter Schritt wird protokolliert; die
  Queue macht mit dem **nächsten Eintrag** weiter (das `reset` davor entwirrt auch einen
  verkeilten Endpoint). Schlägt `eval` fehl, wird `judge` für diesen Eintrag übersprungen.
- **per-Step-Timeout = der Watchdog:** deckt sowohl Crash (Exit ≠ 0) als auch Hänger
  (Prozess lebt, 0 % CPU, kein Exit → der diagnostizierte Fall) ab. Bei Überschreitung:
  `terminate()` (SIGTERM) → `poll`-Gnadenfrist (z. B. 10 s) → `kill()` (SIGKILL).
- Großzügige Default-Timeouts, da der Judge real ~4 h/Bundle braucht; ein *echter* Hänger
  kostet im schlimmsten Fall einen Slot, nicht die Nacht. Beide Timeouts konfigurierbar.

## Output & Resumierbarkeit

- **Per-Eintrag-Bundles** liegen wie gewohnt in `runs/` (Top-Level) → bestehende Discovery,
  `aggregate`, GUI finden sie **unverändert**.
- **Queue-Run-Dir** `runs/<ts>_queue/`:
  - `summary.json` — maschinenlesbar, **inkrementell** nach jedem Eintrag geschrieben:
    pro Eintrag `{index, model_id, config, pack, bundle_dir, eval_status, judge_status,
    eval_seconds, judge_seconds, error?}` + Queue-Metadaten (Start, Quelle-yaml-Hash).
  - `summary.md` — menschenlesbarer Morgen-danach-Report (Tabelle: Modell · eval · judge ·
    Dauern · Bundle-Pfad/-Status).
- **`--resume <queue-dir>`:** liest `summary.json`, **überspringt Einträge, die schon
  erfolgreich abgeschlossen** sind (Bundle vorhanden + Status `ok`), fährt mit dem Rest fort.
  (Die zugrundeliegenden `eval`/`judge` sind zusätzlich einzeln resumierbar; Queue-Resume =
  Skip-fertige-Einträge — simpel + robust.)

## Koexistenz mit der GUI-One-Run-Lock

Die GUI-Control-Plane erzwingt „nur ein Messlauf gleichzeitig" über ein transientes
`run.json`-Sentinel (`RunRegistry`). Die Nacht-Queue läuft **sequenziell** und ist der
*einzige* vorgesehene nächtliche Treiber → interne Kollision ausgeschlossen. Eine
parallele GUI-/Hand-Auslösung mitten in der Nacht ist unwahrscheinlich (unbeaufsichtigt).
Für v1 ist das akzeptabel; ob die gespawnten `eval`/`judge`-Subprozesse das Sentinel selbst
schreiben (damit die GUI-Übersicht sie als „running" zeigt und der Lock greift), klärt der
Plan — falls billig, wird es mitgenommen, sonst v2.

## `--check` (Verify-Mode) — die JIT-Machbarkeits-Probe

Für **jedes distinkte Modell** über alle Einträge:
1. reset + settle.
2. **ein** winziger Generations-Request (Reuse `preflight`-Maschinerie, kleines `max_tokens`).
3. Erfassen: lädt das Modell? kommt sichtbarer Content? RAM vor/nach reset (sah settle einen
   Drop?) und RAM nach Load (zeigte sich ein Modell-Delta?).

Kein Matrix-Lauf, kein judge. Ausgabe: Tabelle + `check.md`. **Beweist die LM-Studio-JIT-Kette
(Load + Eviction + RAM-Settle), bevor eine echte Nacht startet** — der im Handoff markierte
Feasibility-Hinge.

## Module & Platzierung

- **Neu `touchstone/runqueue.py`** (pure Orchestrierung, NICHT `queue.py` — das würde das
  stdlib-`queue` shadowen):
  - pydantic-Modelle `QueueSpec` / `QueueEntry` / `QueueDefaults` / `EntryResult`.
  - `load_queue(path) -> QueueSpec` (+ Existenz-/ModelSpec-Validierung).
  - `wait_until_settled(ram_poll, sleep, *, epsilon_mb, plateau_polls, poll_interval_s,
    timeout_s) -> SettleOutcome` — pure, **DI** von `ram_poll`/`sleep`/`clock`.
  - `run_dir_for(ts, model, pack_id) -> str` — deterministischer Slug.
  - `render_summary_md(spec, results) -> str` / `summary_json(...)`.
  - `entries_to_run(spec, prior_results) -> list[QueueEntry]` — Resume-Skip-Logik (pure).
  - `run_queue(spec, *, spawn, ram_poll, sleep, clock, run_dir, on_event=…)` — die
    Orchestrierungsschleife; **DI** von `spawn` (Subprozess-Start+Warten+Timeout-Kill),
    `ram_poll`, `sleep`, `clock` → ohne Server/sudo testbar (wie `stream_once(clock=…)`,
    `run_benchmark(sampler=…)`).
- **`touchstone/cli.py`** — dünner `queue`-Command, der die echten Implementierungen
  injiziert: realer Subprozess-Spawn (`subprocess.Popen` + `wait(timeout=…)` + Kill-Eskalation),
  realer psutil-RAM-Poll, reale `time`-Funktionen. Verdrahtet `--check`/`--resume`.
- Wächst `runqueue.py` zu groß (Summary-Render + Orchestrierung + Schema), wird Summary-Render
  in `runqueue_summary.py` ausgelagert — erst splitten, wenn nötig.

## Tests (DI-Ethos des Repos)

Pure/Unit (ohne Server/sudo):
- `load_queue`: gültige/ungültige yaml, fehlende `config`/`pack`/`judge_config`, String- vs
  Objekt-`model`, `defaults`-Vererbung + per-Eintrag-Override, leeres/Mehr-als-ein-Modell → Fehler.
- `wait_until_settled`: Plateau erkannt (synthetische RAM-Sequenz), Timeout-Pfad, sofort-stabil.
- `run_dir_for`: Slug-Determinismus + Sonderzeichen-Sanitisierung (`/` in model-id → sicher).
- `entries_to_run`: Resume überspringt `ok`-Einträge, wiederholt `failed`/`timeout`, leere
  prior-results = alle.
- `render_summary_md` / `summary_json`: Felder, Stati, Dauern, Fehlertext.
- `run_queue` mit **Fake-Spawn**: Happy-Path (eval+judge ok), eval-Fehler → judge übersprungen,
  Timeout → Schritt `timeout` + nächster Eintrag läuft, reset-Fehler → warnen+weiter,
  inkrementelles `summary.json` nach jedem Eintrag, `judge_config` fehlt → nur eval.
- **Config-Validierung:** ein Beispiel-`queue.yaml` (ausgeliefert, z. B. `queue.example.yaml`)
  muss `load_queue` bestehen (gespiegelt zu `tests/test_config.py`).

Kein E2E gegen einen echten Endpoint im Test-Suite (wie der Rest); `--check` ist das manuelle
E2E-Werkzeug für den realen LM-Studio-Lauf.

## Betroffene/neue Dateien

- **neu** `touchstone/runqueue.py` — Orchestrierung + Schema + pure Helfer.
- `touchstone/cli.py` — `queue`-Command (Spawn/RAM/Clock-Verdrahtung, `--check`/`--resume`).
- **neu** `queue.example.yaml` — Beispiel-Queue (validiert im Test).
- **neu** `tests/test_runqueue.py` — die obigen Unit-Tests.
- `AGENTS.md` — `queue`-Command in der Command-Liste + ein Gotcha (reset/settle, „100 % ≠
  fertig", Watchdog-Timeouts).
- `docs/reference/` — kurze `queue`-Referenz (optional, falls Zeit).

## Offener Verifikations-Punkt (vor erster echter Nacht, via `--check`)

`lms unload --all` muss auf der Maschine existieren/funktionieren und das Modell tatsächlich
evicten; LM Studio muss eine andere `model-id` JIT laden. Beides empirisch mit `--check`
belegen, bevor eine Nacht-Queue scharf läuft. Existiert `lms` nicht, ist `reset_command`
konfigurierbar (anderes Unload-Kommando) — die settle-Logik bleibt robust.
