# ADR-0014: GUI Out-of-Process-Control-Plane + runs/ = SSOT

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** GUI

## Kontext

`touchstone` war ein reines CLI-Werkzeug; die GUI-Steuerzentrale (`touchstone gui`) soll Konfigurieren, Starten/Stoppen und Live-Zusehen aus dem Browser ermöglichen, also Läufe selbst auslösen statt nur anzuzeigen (Spec §1). Damit entsteht die Frage, **wo** die Messung läuft, sobald ein langlebiger Webserver mit im Spiel ist.

Die Kernkraft ist die Mess-Sauberkeit: Der Harness ist darauf gebaut, Latenz und Speicher unverfälscht zu messen — Speicher/Throttling aus dem Request-Thread zu schätzen ist verfälscht, weil derselbe Thread, der auf die Antwort wartet, den Host-Zustand nicht neutral messen kann (siehe `design-decisions.md`). Liefe die persistente Steuerzentrale im selben Prozess wie die Messung, teilte der Mess-Thread Event-Loop, Garbage-Collection und die schweren Web-Deps (FastAPI etc.) mit dem Server, und die Latenz wäre nicht mehr sauber messbar (`design-decisions.md`; Spec G3).

Zweite Kraft: Was darf die GUI persistieren? Die Repo-Verfassung lautet „persistierter Output = nur MD/CSV"; ein transientes File darf nicht zur „Wahrheit" promotet werden (Spec G4/G10).

## Entscheidung

Die GUI ist eine **Out-of-Process-Control-Plane**: `touchstone gui` **spawnt** die Messung als eigenen Subprozess (`[sys.executable, "-m", "touchstone", "eval"/"judge", …]`), genau wie die CLI heute den Host-Sampler spawnt, und beobachtet sie nur über das Dateisystem (Tail von `events.jsonl`) — kein In-Process-`run_eval`-Aufruf (Spec G3; `control.py` `RealProcessLauncher.spawn` ruft `subprocess.Popen([sys.executable, "-m", "touchstone", *argv])`).

`runs/` bleibt die **Single Source of Truth** (MD/CSV/JSONL-Ledger); ins Ledger schreiben ausschließlich die Mess-Subprozesse. Die GUI persistiert keine Mess-Wahrheit, sondern nur einen flüchtigen **Run-Sentinel** `run.json` (`SENTINEL_NAME = "run.json"` in `control.py`) im aktiven run_dir. Der Sentinel trägt drei Rollen zugleich: run_dir-Handle, Cross-Process-Lock (nur ein Mess-Lauf gleichzeitig, überlebt einen GUI-Neustart) und Discovery-Anker für laufende/abgestürzte Läufe (Spec G4/G8/§6; Essay).

Der Launcher wählt das run_dir host-seitig (`RunRegistry._new_run_dir` → `{ts}_eval_{pack_id}`), schreibt den Sentinel mit `state="running"` **vor** dem Spawn, reicht es per `--run-dir` an den Subprozess durch und aktiviert die Event-Writer per `--emit-events` ohne webmon-Spawn (`control.py` `start_eval`/`start_judge`; Spec §3). Die GUI ist Opt-in über das `[gui]`-Extra und lädt nie im Mess-Prozess (Spec G9).

## Erwogene Alternativen

- **In-process Control-Plane (GUI ruft `run_eval` direkt)** — verworfen, weil der Mess-Thread dann Event-Loop, GC und GUI-Deps mit dem Webserver teilte und genau die saubere Latenz-/Speichermessung bräche, für die der Harness gebaut ist (Spec G3; `design-decisions.md`).
- **Mess-Wahrheit/Steuer-Zustand in der GUI persistieren statt `runs/` als SSOT** — verworfen, weil das „persistierter Output = nur MD/CSV" verletzte und ein transientes File zur „Wahrheit" promotete (Spec G4/G10).
- **Reiner in-memory Lauf-Guard (ohne On-Disk-Sentinel) für „nur ein Lauf"** — verworfen, weil er bei GUI-Neustart bräche (verwaister Subprozess + leere Registry → zweiter Lauf); der Sentinel-Lock auf Platte überlebt den Neustart (Spec G8/§9; `control.py` `_active_run_dir` scannt On-Disk-Sentinels).
- **Subprozess mit `--web` starten (zweiter HTTP-Server als Live-Quelle)** — verworfen; stattdessen tailt der GUI-Server selbst `events.jsonl` (Spec §3: „kein webmon-Spawn", „kein zweiter HTTP-Server").
- **CDN-/npm-Frontend statt build-frei** bzw. neue Deps im Kern — verworfen; Deps liegen isoliert im optionalen `[gui]`-Extra, vendored JS/CSS, kein Build-Schritt (Spec G2/G9).

## Auswirkungen

- Positiv: Der Mess-Subprozess bleibt entkoppelt und GUI-dep-frei; die Latenz bleibt sauber messbar, während ein modernes build-freies Frontend obendrauf sitzt (Spec G3; Essay).
- Positiv: Der Default-Pfad ohne `--web`/`--emit-events` bleibt byte-identisch — CLI/Tests/CI brauchen FastAPI nie (Spec G9/§5; Essay).
- Positiv: Der On-Disk-Sentinel macht „nur ein Lauf gleichzeitig" und die Discovery (running/crashed) crash- und neustart-robust, ohne in-memory-PID-Recovery (Spec G8/§9; `control.py` `is_active`/`_active_run_dir`).
- Trade-off / Restgrenze: Die GUI schreibt ein zusätzliches transientes File (`run.json`) ins run_dir; es ist bewusst non-SSOT und aus Discovery/Export ausgeschlossen, fügt aber Steuer-State neben dem Ledger hinzu (Spec G4/G10; `app.py` `/export`-Allowlist enthält `run.json` nicht).
- Trade-off / Restgrenze: Zwei additive CLI-Argumente (`--run-dir`, `--emit-events`) und Refactors in `cli.py` (Event-Writer von webmon entkoppeln, `master_rows`-Helper extrahieren) sind nötig — strukturelle Arbeit, kein bloßes Flag (Spec §3/§5).
- Trade-off / Restgrenze: Da der GUI-Server den Subprozess nie `wait()`et, kann ein fertiger Lauf als Zombie verbleiben; deshalb schreibt die CLI ihren Terminal-State selbst (`finalize_sentinel`) und die Liveness-Prüfung erkennt Zombies explizit via psutil (`control.py` `_pid_alive`/`finalize_sentinel`).
- Restgrenze: Single-User, an `127.0.0.1` gebunden; Auth/Multi-User/Remote sind bewusst nicht in v1 (Spec §11; `app.py` `TrustedHostMiddleware` + CSRF-Origin-Guard auf localhost).

## Belege & Links

- Spec: `docs/superpowers/specs/2026-06-21-gui-steuerzentrale-design.md` (G3, G4, G8, G9, G10, §3, §6, §9) · Code: `touchstone/gui/control.py`, `touchstone/gui/app.py` · Essay: `docs/explanation/design-decisions.md`
- Tests: `tests/` (laut Spec §10/§12: `control.py`-Registry/G8-via-Sentinel, run_dir/Sentinel-vor-Spawn, FastAPI-`TestClient`-Routen; Kern ohne `[gui]`)
