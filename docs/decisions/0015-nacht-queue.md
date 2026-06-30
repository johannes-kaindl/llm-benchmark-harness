# ADR-0015: Nacht-Queue (ein Modell/Run, reset+settle, Watchdog, --check)

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Betrieb

## Kontext

Mehrere Modelle über Nacht **unbeaufsichtigt** zu benchmarken stößt auf zwei Probleme: (1) Mehrere
Modelle in *einem* Run blähen das RAM-Delta ab Modell 2 auf (geteilte Baseline + kein Unload zwischen
Modellen — siehe ADR-0004 / Memory `multimodel-ram-confound`); (2) unbeaufsichtigte Subprozesse können
hängen und die ganze Kette blockieren. Es braucht einen Orchestrator, der jedes Modell isoliert misst
und Hänger übersteht.

## Entscheidung

`touchstone queue` — ein **sequenzieller Subprozess-Orchestrator**. Pro Eintrag:
`reset_command` (Default `"lms unload --all"`) → konditionsbasiertes RAM-**Settle** (`wait_until_settled`:
Plateau via `SettleSpec` = timeout_s/plateau_polls/poll_interval_s/epsilon_mb) → `eval` als eigener
Subprozess → optional `judge`. **Ein Modell pro Eintrag** mit frischer RAM-Baseline. continue-on-error +
**per-Step-Watchdog** (`StepTimeouts` eval/judge: SIGTERM→SIGKILL). Atomisches, inkrementelles
`summary.json`/`summary.md`. **Entry-granularer `--resume`** (fertige Einträge übersprungen). `--check`
verifiziert die LM-Studio-JIT-Load+Eviction-Kette **vor** dem ersten echten Lauf. `load_queue` lehnt
doppelte `(config, pack, model)`-Einträge ab (sonst stilles Bundle-Überschreiben). `runqueue.py` ist
**pure Logik + DI** (Schema, settle-wait, argv-Builder, summary, resume, step-classification); der dünne
`queue`-Command verdrahtet `subprocess`/`psutil`/`time`. Fertigstellung wird am **Subprozess-Exit +
Finalize-Artefakten** erkannt, nie an einem Fortschrittsbalken.

## Erwogene Alternativen

- **Mehrere Modelle pro Run** — verworfen: geteilte Baseline + kein Unload → aufgeblähtes Delta ab Modell 2
  (ADR-0004, `multimodel-ram-confound`). Ein Modell/Eintrag = saubere, vergleichbare Zahl.
- **`eval --resume` pro Eintrag** — verworfen: `eval` verwirft beim Resume das `--models-json`-Override
  (gilt nur beim Nicht-Resume-Start) → falsches Modell. Daher baut `build_eval_argv` **immer** einen frischen
  `--run-dir`; Queue-Resume ist entry-granular (ein unvollständiger Eintrag läuft von vorn).
- **Engine-spezifischer Reset im Code** — verworfen: `reset_command` ist ein konfigurierbares Shell-Kommando
  (Daten, kein Engine-Branch — konsistent mit ADR-0001).
- **Fortschritts-/Prozent-basierte Fertig-Erkennung** — verworfen: die holistische Judge-Phase liefert ~0
  Events („100 % ≠ fertig"); Exit-Code + Finalize-Artefakte sind das verlässliche Signal.

## Auswirkungen

- Positiv: unbeaufsichtigte Multi-Modell-Nacht mit **sauberem, maschinen-vergleichbarem Modell-Delta** je
  Modell; ein Hänger killt nur den Eintrag (Watchdog), nicht die Kette; `--check` beweist die JIT-Kette vorab.
- Trade-off / Restgrenze: rein **sequenziell** (kein Parallelismus — bewusst, für RAM-Isolation, also langsam).
  Ein *nur*-holistischer Judge-Fehler endet sauber (Exit 0) mit `_error`-Report → die Queue re-judged ihn
  **nicht** automatisch; ein manuelles `judge` heilt ihn (siehe ADR-0010).

## Belege & Links

- Spec: `docs/superpowers/specs/2026-06-27-nacht-queue-design.md` · Code: `touchstone/runqueue.py`,
  `touchstone/cli.py` (`queue`) · Beispiel: `queue.example.yaml` · Tests: `tests/test_runqueue.py`
- Verwandt: ADR-0002 (entkoppelte Producer), ADR-0004 (Modell-Delta), ADR-0010 (Judge-Runaway-Guard)
