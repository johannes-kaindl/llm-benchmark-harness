---
paths:
  - "touchstone/runqueue.py"
  - "queue.example.yaml"
  - "tests/test_runqueue.py"
  - "docs/how-to/nacht-queue-fahren.md"
  - "docs/decisions/0015-nacht-queue.md"
---

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
