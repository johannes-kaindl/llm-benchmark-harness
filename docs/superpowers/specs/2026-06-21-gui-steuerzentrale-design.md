# GUI-Steuerzentrale (`ramcheck gui`) — Design (Ink. 6)

**Datum:** 2026-06-21
**Status:** ratifiziert (Brainstorming abgeschlossen) + nach adversarialer Code-Review geschärft — vor Implementierungsplan
**Scope:** v1 — **Walking Skeleton** einer persistenten, lokalen Benchmarking-Steuerzentrale: ein dünner, durchgehender Faden durch alle 7 Stationen (konfigurieren → starten → live zusehen → auswerten → vergleichen → exportieren), der die Architektur end-to-end beweist.
**Baut auf:** dem `eval --web` / `judge --web` Monitor-Fundament ([`2026-06-20-web-live-monitor-design.md`](2026-06-20-web-live-monitor-design.md), [`2026-06-20-judge-web-monitor-design.md`](2026-06-20-judge-web-monitor-design.md)) — `tail.read_new`, `events.build_view`, `judge_events.build_view`, `loadview.latest_load`, das `_SamplerProcess`/`_WebMonitorProcess`-Spawn-Muster.

## 1 · Ziel & Motivation

Heute ist `ramcheck` ein CLI-Werkzeug: Config von Hand editieren, `uv run ramcheck eval/judge`, danach `scorecard.md` / `aggregate.md` lesen, Ergebnis ins Vault übernehmen. Drei Dinge kann dieser Workflow **genuin nicht**, und genau sie rechtfertigen ein WebUI (reine Textanzeige täte es nicht):

1. **Verknüpfte Transparenz** — die Bewertungslogik ist über Pack-YAML, Judge-Code und drei MD-Dateien verstreut; den *Zusammenhang* („dieser Prompt → gegen diese Flags geprüft → fließt mit Gewicht ×3 in Dimension Q6 → Q6 ≤ 2 ist die K.-o.-Regel") sieht niemand. Das ist ein verlinkter Graph, kein Fließtext.
2. **Echte Daten-Visualisierung** — `resources.jsonl` ist eine 2-Hz-Zeitreihe (Speicherdruck/Throttle über die Laufzeit = ein *Plot*); TTFT-P50/P95-Verteilungen, Score-Histogramme, die Cross-Machine-Matrix — alles heute platte Zahlen in MD-Tabellen, dabei sind es Verläufe und Verteilungen, die man *sehen* will.
3. **Steuerung** — Läufe konfigurieren + starten/stoppen aus dem UI statt Config-Handarbeit + `uv run`-Befehle.

Die **Steuerzentrale** macht aus `ramcheck` ein kohärentes Werkzeug mit einem durchgehenden Workflow:

> **konfigurieren → starten → live zusehen → auswerten → vergleichen → exportieren**

— mit dem Pack-Innenleben transparent, Perf + Quali + Cross-Machine an einem Ort, echten Plots statt Zahlen-Tabellen und Start/Stop aus dem Browser.

**Zielgruppe (ratifiziert: c):** primär das *persönliche Cockpit* (M5, bald M1↔M5 cross-machine) — darf Vorwissen voraussetzen, optimiert für Effizienz. Aber das *publizierbare* Ziel (Jays Vision: viele Leute steuern vergleichbare Läufe bei) wird von Anfang an nicht verbaut: das Tool soll sich selbst erklären können, Ergebnisse bleiben sauber teilbar.

## 2 · Ratifizierte Entscheidungen (mit Begründung)

| # | Entscheidung | Begründung |
|---|---|---|
| **G1** | **Ein kohärentes Produkt**, kein „Read- vs. Write-Hälfte". Die 7 Stationen sind ein Workflow, nicht zwei Lager. | Transparenz und Steuerung sind keine Gegensätze — ein gutes Werkzeug hat beides. Künstliche Hälften-Wahlen verworfen. |
| **G2** | **Stack B — Python-rich, build-frei**: FastAPI/Starlette + HTMX + Alpine.js + Tailwind. Neue Deps **isoliert** in einem optionalen `[gui]`-extra. | Erfüllt #5 („best practices Frontend") ohne npm/Vite-Toolchain; bleibt Python-zentrisch (eine Sprache, eine uv-Build-Story); volle Design-Kontrolle (am besten für das publizierbare Ziel); HTMX+SSE ist die direkte Evolution des `webmon`-Patterns. |
| **G3** | **Out-of-process Control-Plane**: die GUI **spawnt** `ramcheck eval/judge` als Subprozesse (`[sys.executable, "-m", "ramcheck", …]`), wie heute die CLI Sampler/webmon spawnt. | **Präzise Invariante** (nicht „zero-dep im Hot-Path" pauschal): der **Host-Sampler bleibt entkoppelt**, **Memory wird nie aus dem Request-Thread geschätzt**, und **GUI-Deps laden nie im Mess-Prozess**. (Der pro-Zelle Event-Write existiert schon heute bei `--web` und liegt zwischen Zellen, nicht im Token-Stream.) In-process (GUI ruft `run_eval` direkt) würde den Mess-Thread mit Webserver-Event-Loop/GC/GUI-Deps teilen → genau das, was das Tool vermeiden will. |
| **G4** | **`runs/` = SSOT** (MD/CSV/JSONL-Ledger). Die GUI persistiert **keine Mess-Wahrheit**. Ihr Steuer-Zustand lebt in einer **in-memory Lauf-Registry**, gespiegelt auf Platte als **Run-Sentinel** `run.json` im aktiven run_dir (transient, non-SSOT, siehe §6). | Wahrt „persistierter Output = nur MD/CSV". Das Sentinel ist transienter Steuer-State (kein Mess-Artefakt) — crash-/restart-robust auf Platte, damit G8/Discovery einen GUI-Neustart überleben. |
| **G5** | **Walking Skeleton** als v1: dünner Faden durch **alle** Stationen, riskanter greenfield-Kern (Steuerung + Live-Tail, Stationen 3+4) **zuerst** durchgestochen. Read-Stationen rendern vorhandene pure Funktionen/Dateien. | Hält das Produkt kohärent, beweist die Architektur end-to-end, vermeidet die Hälften-Falle. |
| **G6** | **`judge`-Start gleich in v1**. | Mechanisch identisch zu eval (gleiches spawn/tail). Erst mit judge ist der Workflow rund — eine Scorecard entsteht durch judge. |
| **G7** | **Geteilte Datenschicht mit `webmon`, getrennte Präsentation.** CLI `eval --web` / `judge --web` bleibt **unangetastet**. Die GUI verwendet das **SSE-Datenmodell verbatim** (`tail.read_new` + `events/judge_events.build_view().as_dict()`). | Aggregations-Mathematik (Histogramm, ETA, Dedup) wird **nicht dupliziert**. **Aber:** die Präsentation ist **net-new** — `webmon` rendert eine hartkodierte `INDEX_HTML`-Vollseite mit Inline-JS; die HTMX/Alpine/Tailwind-Fragmente sind Neubau (siehe G11). |
| **G8** | **Nur ein Mess-Lauf gleichzeitig** (verfassungs-relevant), durchgesetzt per **Filesystem-Lock** (das Run-Sentinel `run.json`), nicht nur durch die in-memory Registry. | Parallele Läufe streiten um RAM/CPU und verfälschen sich *gegenseitig* — Mess-Sauberkeit. Ein reiner in-memory Guard bräche bei GUI-Neustart (verwaister Subprozess + leere Registry → zweiter Lauf). Der Lock überlebt den Neustart. |
| **G9** | **GUI ist Opt-in** über `[gui]`-extra + Befehl `ramcheck gui`. Ohne Extra läuft `ramcheck` unverändert (CLI/Tests/CI brauchen FastAPI nie); `ramcheck gui` ohne Extra → freundlicher Installations-Hinweis statt Crash. | Kern bleibt der thin harness; GUI lädt nie im Hot-Path. |
| **G10** | **Event-Dateien sind transient, non-SSOT.** `events.jsonl` / `judge_events.jsonl` sind Monitor-/Tail-Instrumentierung, **nicht** Teil des Bundle-Ledgers — aus Discovery & Export **ausgeschlossen**. Der GUI-gespawnte eval schreibt `events.jsonl` **truncate-per-spawn** (wie judge es schon tut), nicht append. | Verhindert, dass ein transientes File zur „Wahrheit" promotet wird (AGENTS.md). Truncate-per-spawn behebt zugleich den `finished=True`-Latch-Bug auf dem Resume-Live-Pfad (siehe §9). |
| **G11** | **Frontend-Präsentation ist net-new Arbeit** und der eigentliche Umfang des Skeletts; wiederverwendet werden nur die **puren Daten-/Render-Funktionen** der Logikschicht. | Ehrlichkeit über den Aufwand: Templates, htmx-Swaps, Alpine-Cards, Tabellen-CSS sind neu. „Reuse" gilt für `build_view`/`scorecard_mod`/`aggregate`, nicht für UI. |

## 3 · Prozess-Topologie

```
 ramcheck gui [--port 0] [--no-open]
 │
 │  GUI-Server (FastAPI · langlebig · [gui]-extra) — Browser: HTMX/Alpine/Tailwind über HTTP/SSE
 │
 ├─ Stationen-Routen (7)           ── rendern HTMX-Fragmente (Jinja2)                      [neu, G11]
 ├─ Lese-/Render-Schicht           ── load_pack · scorecard_mod · aggregate · Loader       [wiederverwendet]
 ├─ Live-View-Adapter              ── tail.read_new + events/judge_events.build_view → SSE  [Datenmodell wiederverwendet, G7]
 └─ Control-Plane + Lauf-Registry  ── run_dir host-seitig wählen · Run-Sentinel · ein Lauf  [neu, G3/G4/G8]
        │ spawn:  [sys.executable, "-m", "ramcheck", "eval",                 │ stop: SIGTERM→wait(5)→kill
        │          "--pack", …, "--config", …, "--run-dir", <host-gewählt>,  ▼
        │          "--emit-events"]   (kein webmon-Spawn)         ┌────────────────────────────────────┐
        ▼                                                          │ runs/<ts>_eval_<pack>/  = SSOT       │
   ┌──────────────────────────────┐   write (SSOT-Ledger)         │  bundle.json · responses.jsonl ·     │
   │ Mess-Subprozess              │ ───────────────────────────▶ │  judgements.jsonl · resources.jsonl ·│
   │ reiner Mess-Loop, Sampler    │   write (transient, G10)      │  perf.csv · scorecard.md · scores.csv │
   │ entkoppelt, GUI-dep-frei     │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ▶ │  + events.jsonl (transient)          │
   └──────────────────────────────┘                              │  + run.json (Sentinel, transient)    │
        ▲   GUI tailt events.jsonl + liest Ledger ───────────────┘                                       │
```

**Warum `--run-dir` + `--emit-events` (zwei neue, additive CLI-Argumente):**
- **`--run-dir <path>`** (eval & judge): heute erfindet `eval_cmd` das finale run_dir selbst (`base_out / f"{ts}_eval_{pk.id}"`); `--out` setzt nur den Parent, und das run_dir wird nur via Rich-`console.print` ausgegeben (nicht maschinenlesbar). Damit die GUI den **Tail-Pfad beim Spawn kennt** (Station 3→4), **wählt der Launcher das run_dir host-seitig**, legt es an, schreibt das Sentinel und reicht es per `--run-dir` exakt durch. (Behebt den Blocker.)
- **`--emit-events`** (eval & judge): aktiviert die Event-Writer **ohne** webmon zu spawnen — die GUI tailt selbst. Das ist **neue strukturelle Arbeit**, kein bloßes „Flag aktivieren" (siehe §6).

Der Subprozess wird **nicht** mit `--web` gestartet (kein zweiter HTTP-Server). Der Live-Fortschritt kommt aus `events.jsonl`, das der GUI-Server selbst tailt.

## 4 · Die 7 Stationen — Walking-Skeleton-Schnitt

⚡ = greenfield-Kern (zuerst). Read-Stationen rendern vorhandene pure Funktionen — aber **Status & Urteil werden berechnet, nicht aus `bundle.json` gelesen** (siehe §8).

| Station | **v1 — Skeleton** | Vertiefung (spätere Specs) |
|---|---|---|
| **1 · Übersicht** | Discovery über `runs/` (§8) → Tabelle: Pack, Modelle, Datum, **abgeleiteter Status** (running/judged/eval-only/crashed), **berechnetes Urteil-Badge** (via `scorecard_mod`, nur wenn judged); Klick → Ergebnis | Filter/Suche/Sortierung, Live-Status-Badges |
| **2 · Pack-Explorer** | `load_pack()` → Dimensionen/Gewichte/K.-o./Prompt-Varianten/Prompts+Green-Red-Flags als Read-Tabellen | verlinkter Graph (Prompt→Flag→Dimension→K.-o.), Varianten-Diff |
| **3 · Konfig + Steuerung** ⚡ | **vorhandenes** Pack + Config aus Dropdown → **eval starten** / **stoppen** / **fortsetzen** (`--resume`); **judge-Config** wählen → **judge starten** (G6) | Config-Felder im UI editieren, Modell-Auswahl, Live-Validierung, Pack-Editor |
| **4 · Live-Monitor** ⚡ | `events.jsonl` / `judge_events.jsonl` tailen → Fortschritt (done/total, Balken; eval zusätzlich Last via `loadview`) via SSE | volles Score-Histogramm, Verdict-Stream, ETA-Feinschliff |
| **5 · Ergebnis-Ansicht** | ein Bundle: Kern-Zahlen (Tech-Specs, Master-%, Urteil, K.-o.) via `scorecard_mod` in **Jinja-Tabellen** (kein `.md`-Render — siehe §11) | echte **Plots**: RAM/Throttle-Zeitreihe, TTFT-Verteilung, Score-Histogramm; rohe `scorecard.md`-Ansicht |
| **6 · Vergleich** | `aggregate.load_all_scores` + `aggregate` über `runs/` → Hardware×Qualität-Tabelle | interaktive Matrix, Cross-Machine-Charts |
| **7 · Export** | bestehende `scorecard.md` / `scores.csv` / `aggregate.md` als Download (Event-/Sentinel-Dateien ausgeschlossen, G10) | Export aus jeder Ansicht, weitere Formate, gefilterte Exporte |

App-Shell: Sidebar-Navigation (7 Stationen) + Hardware/Endpoint-Status; laufende Messung als Karte oben (Fortschritt + Stop + Live-Last); Bundle-Liste mit Urteil-Badges (inkl. K.-o.-Markierung). Look: Tailwind, dicht genug fürs Cockpit.

## 5 · Modul-Struktur

Neues Sub-Package `ramcheck/gui/` — **nur importiert, wenn der GUI-Server läuft** (das `[gui]`-extra liefert die Deps). Der Kern (`runner`, `qualrun`, `judge`, `scorecard`, …) importiert `gui` **nie**.

```
ramcheck/gui/
  __init__.py
  app.py        FastAPI-App-Factory: Routen der 7 Stationen, mountet static/ + templates/
  control.py    Control-Plane: Lauf-Registry (Zustandsmaschine) + ProcessLauncher-Protocol
                (injizierbar → im Test ein Fake); run_dir host-seitig wählen; Run-Sentinel
                schreiben/lesen; spawn/stop/resume; erzwingt G8 via Sentinel-Lock
  bundles.py    Discovery + Detail-Aufbereitung (read): klassifiziert run_dirs (§8),
                berechnet Status + Urteil via geteiltem master_rows-Helper (aus cli.py
                herausfaktorisiert), liest Ledger via vorhandene Loader/Renderer
  live.py       Live-View-Adapter: tail.read_new + events/judge_events.build_view → SSE-Fragmente (G7)
  templates/    Jinja2 (HTMX-Fragmente, App-Shell)            [net-new, G11]
  static/       vendored htmx.min.js, alpine.min.js, CSS (offline-fähig, kein Build-Schritt)
```

`cli.py` bekommt einen `gui`-Befehl (lazy import von `ramcheck.gui.app`; ImportError → „installiere `pip install -e .[gui]`"), startet uvicorn und öffnet den Browser (`--no-open` unterdrückt).

**`pyproject.toml`:** `[project.optional-dependencies] gui = ["fastapi", "uvicorn", "jinja2", "python-multipart"]`. Markdown-Renderer **bewusst nicht** (Station 5 rendert Zahlen, nicht `.md` — §11). Vendored JS/CSS statt CDN (offline-fähig, build-frei).

**Refactor in `cli.py` (v1-Voraussetzung, kein Detail):**
- **Event-Writer von Monitor-Spawn entkoppeln:** Heute werden `_eval_event_writers` / `_judge_event_writers` **nur** innerhalb des `with _live_monitor(...)`-Blocks (hinter `if web:`) verdrahtet. Für `--emit-events` (Writer ohne Monitor) müssen `eval_cmd`/`judge` so umstrukturiert werden, dass „Event-Writer öffnen" von „webmon spawnen" trennbar ist. **Garantie:** ohne Flag (weder `--web` noch `--emit-events`) bleibt der Pfad **byte-identisch** (kein Event-File, keine Callbacks) — abgesichert durch die existierenden `test_eval_web` / `test_cli_judge_web`-Regressionstests.
- **`--run-dir <path>`** (eval & judge): exaktes run_dir von außen vorgeben statt selbst-erfinden.
- **`master_rows`-Helper herausfaktorisieren** (heute `cli._master_rows`): wird von Station 1 **und** 5 gebraucht, um das Urteil pro Bundle zu (re)berechnen → in ein wiederverwendbares, GUI-importierbares Modul (z. B. `scorecard.py` oder ein kleines `report`-Helper) heben.

## 6 · Control-Plane, Run-Sentinel & Lauf-Registry (der greenfield-Kern)

**run_dir host-seitig (Blocker-Fix):** `ProcessLauncher.start_eval` berechnet das run_dir **selbst** (`output_path()/f"{ts}_eval_{pack_id}"`), legt es an, schreibt das Sentinel, und spawnt mit `--run-dir <dieses>`. Damit ist `RunHandle.run_dir` beim Spawn bekannt und der `live.py`-Tail-Pfad deterministisch.

**Run-Sentinel `run.json`** (transient, G4/G10) — host-seitig ins run_dir geschrieben, **bevor** der Subprozess startet, gelöscht/als beendet markiert beim sauberen Ende:
`{ kind: "eval"|"judge", run_dir, pid, pack_path, config_path|judge_config_path, started_ts, state }`.
Trägt **drei** Funktionen zugleich: (1) **run_dir-Handle**, (2) **Cross-Process-Lock** für G8, (3) **Discovery-Anker** für laufende/abgestürzte Läufe (§8).

**Lauf-Registry** (in-memory, gespiegelt aufs Sentinel): hält **höchstens einen** aktiven `RunHandle`; `state ∈ {running, finished, failed, stopped}`.

**`ProcessLauncher`-Protocol** (injizierbar, wie `Sampler`) — Felder **pro kind explizit**:
- `start_eval(pack_path, config_path, *, resume_dir: Path|None) -> RunHandle` — Popen `[sys.executable, "-m", "ramcheck", "eval", "--pack", …, "--config", …, "--run-dir", <host>, "--emit-events"]` (+ `--resume <resume_dir>`); `RunHandle{kind="eval", run_dir, pid, pack_id (aus pack), config_path}`.
- `start_judge(bundle, judge_config_path) -> RunHandle` — `[…, "judge", "--bundle", …, "--judge-config", …, "--emit-events"]`; **judge hat kein `--resume`** (Resume = erneuter Aufruf, überspringt schon bewertete Zellen) und **kein `pack_id`-Input** (Pack kommt aus `bundle.json`); `RunHandle{kind="judge", run_dir=bundle, pid, judge_config_path}`. **`--judge-config` ist Pflicht** — fehlt sie, beendet sich judge mit Exit-Code 1; die UI muss eine judge-Config wählen lassen (Station 3).
- `stop(handle)` — SIGTERM → `wait(timeout=5)` → kill (das `_SamplerProcess`-Teardown-Muster).
- `poll(handle)` — `proc.poll()`; `None`→running, `0`→finished, sonst→failed (Exit 1 ohne judge-Config wird als „failed: judge-config fehlt" gemeldet, nicht als stiller Crash).

**G8-Durchsetzung (Sentinel-Lock):** `start_*` lehnt ab, wenn ein **aktives** Sentinel existiert (run.json mit `state=running` und lebendem PID) — auch nach GUI-Neustart, weil das Sentinel auf Platte liegt. Stale Sentinel (PID tot) wird beim Start aufgeräumt.

## 7 · Live-View — Datenmodell wiederverwendet, Präsentation neu (G7/G11)

Der GUI-Live-Endpoint ist ein **eigener** SSE-Stream (FastAPI `StreamingResponse`), der pro Tick:
1. `tail.read_new(events_path, offset)` → neue Zeilen + Offset,
2. `events.build_view(all_events)` bzw. `judge_events.build_view(...)` (die **schon puren** Falter: Histogramm, ETA, Dedup-per-Key, Master-Faltung) — **verbatim wiederverwendet**,
3. das `.as_dict()`-View-Model in ein **neues** HTMX/Jinja-Fragment (oder JSON→Alpine) rendert.

Für **eval** zusätzlich `loadview.latest_load(resources.jsonl)` (RAM/Pressure/Throttle); für **judge nicht** (anderer Endpoint, Bundle-`resources.jsonl` stale — `TAILS_RESOURCES=False`-Logik). **Tail-Offset:** eval `events.jsonl` ist truncate-per-spawn (G10) → Offset startet bei 0 pro Lauf; judge `judge_events.jsonl` ist ebenfalls truncate-per-run mit prior-Replay → Offset 0 bei (Neu-)Start. Kein Byte der Aggregations-Mathematik wird dupliziert; das gerenderte HTML ist net-new (G11).

## 8 · Datenfluss, Discovery & Status-Ableitung

Die GUI ist **downstream-Leser** von `runs/`. **Discovery (`bundles.py`)** ist **keine** flache `bundle.json`-glob, sondern eine **Klassifikation** je run_dir (weil `bundle.json` erst beim erfolgreichen finalize geschrieben wird und `runs/` auch `run`/`embed`-Dirs enthält):

| Befund im run_dir | Klassifikation |
|---|---|
| aktives Run-Sentinel (`run.json`, PID lebt) | **running** (Live-Tail verfügbar) |
| Sentinel vorhanden, PID tot, kein `bundle.json` | **crashed → resumebar** (`--resume <run_dir>`) |
| `bundle.json` + `responses.jsonl`, **kein** `scores.csv` | **eval-only** (judge ausstehend) |
| `bundle.json` + `scores.csv` | **judged** (Urteil berechenbar) |
| nur `raw.csv`/`report.md` bzw. `*_embed/` | **legacy run/embed** → in v1 ignoriert |

**Urteil-Badge** (nur „judged"): **berechnet**, nicht gelesen — `load_pack(pack_path)` + `load_responses_jsonl` + `load_judgements_jsonl`, dann `scorecard.weighted_total / red_flagged_prompts / passes_ko / recommendation` (= der herausfaktorisierte `master_rows`-Helper, §5). `bundle.json` liefert nur das Manifest (pack/models/host/sampling/date), **kein** Urteil und **keinen** Status.

Gelesene Kontrakte je Station:
- **Übersicht/Vergleich:** Sentinel + `bundle.json`; `aggregate.load_all_scores`/`aggregate` über `scores.csv`.
- **Ergebnis:** `load_responses_jsonl`, `load_judgements_jsonl`, `merge.load_samples_jsonl` (die 2-Hz-Zeitreihe), Math via `scorecard_mod.*` (+ `load_pack`).
- **Pack-Explorer:** `load_pack(pack_path)`.
- **Live:** `events.jsonl` / `judge_events.jsonl` (Tail), `resources.jsonl` (eval-Last).
- **Export:** Ledger-Dateien direkt ausliefern bzw. Renderer (`render_scorecard_md`, `render_aggregate_md`, `scores_csv_rows`) erneut aufrufen. **Event-/Sentinel-Dateien sind ausgeschlossen (G10).**

Die GUI schreibt **nur** das Sentinel (transienter Steuer-State); ins Ledger schreiben **ausschließlich** die Mess-Subprozesse.

## 9 · Error-Handling

- **Nur ein Lauf (G8):** zweiter Start bei aktivem Sentinel verweigert — **überlebt GUI-Neustart** (Lock auf Platte). Stale Sentinel (PID tot) wird aufgeräumt.
- **Subprozess-Crash** (OOM, Endpoint weg): `poll()` ≠ `None`/`0` → `state=failed`; Sentinel bleibt → Discovery zeigt **crashed/resumebar**. Dank crash-safe Ledger (`responses.jsonl` append+flush) bietet die UI **„Fortsetzen"**: **eval** via `--resume <run_dir>`; **judge** durch erneutes Starten auf demselben Bundle (kein Flag — überspringt schon bewertete Zellen).
- **Resume-Live-Korrektheit:** der GUI-gespawnte eval schreibt `events.jsonl` **truncate-per-spawn** (G10), wie judge — behebt den `finished=True`-Latch (ein altes `run_done` würde sonst die Live-View fälschlich „fertig" zeigen, während der Resume noch läuft). Schon erledigte Zellen erscheinen in der Ergebnis-Ansicht (aus `responses.jsonl`), nicht im Live-Tail.
- **Stop:** SIGTERM → wait(5) → kill; Lauf bleibt fortsetzbar (try/finally + per-cell-Persistenz existieren). Sentinel → `stopped`.
- **GUI-Neustart bei laufendem Lauf:** Subprozess läuft eigenständig weiter, schreibt sauber nach `runs/`; das **Sentinel auf Platte** stellt Sicht (Discovery=running) **und** G8-Lock nach Neustart wieder her. Kein in-memory-PID-Recovery nötig.
- **Port belegt / `--port 0`:** uvicorn auf gewähltem Port; für Auto-Port den gebundenen Port aus dem uvicorn-`Server`-Socket lesen, **bevor** der Browser geöffnet wird (in-process, kann nicht wie webmon den eigenen stdout lesen — siehe §13).
- **SSE-Robustheit:** Browser-Close beendet den Stream sauber (wie webmon); Server-Task lebt weiter; halbe Schlusszeile beim Tail toleriert.
- **Ohne `[gui]`-Extra:** `ramcheck gui` → freundlicher Installations-Hinweis, kein Traceback.

## 10 · Teststrategie (TDD)

Konsistent mit der Repo-Philosophie (I/O dependency-injected, pure Logik unit-getestet, mypy strict, ruff). **Kern-Tests laufen ohne FastAPI**; GUI-Route-Tests skippen, wenn `[gui]` fehlt.

**Unit (pure, kein Server):**
- `control.py`: Registry-Zustandsmaschine; **G8 via Sentinel** (zweiter Start bei aktivem Sentinel abgelehnt; **stale Sentinel mit totem PID** aufgeräumt + Start erlaubt; Lock **überlebt simulierten Neustart** = neue Registry liest Sentinel); `stop`-Teardown-Sequenz; `poll`-Mapping inkl. judge-config-fehlt→failed — gegen einen **Fake-ProcessLauncher**.
- `control.py` run_dir/Sentinel: Launcher wählt run_dir host-seitig, schreibt Sentinel **vor** Spawn, reicht `--run-dir` durch (Fake verifiziert die Argv).
- `bundles.py`: **Klassifikation** aller fünf Discovery-Fälle (§8) gegen synthetische run_dirs; Urteil-Recompute stimmt mit `scorecard_mod`/`master_rows` überein; legacy run/embed ignoriert; robuste Toleranz fehlender/halber Dateien.
- `live.py`: Tick faltet getailte Events korrekt (delegiert an die getesteten `build_view`); eval tailt Last, judge nicht; **truncate-per-spawn → Offset-Reset** (kein stale `finished`).
- **cli.py-Refactor:** `--emit-events` schreibt Event-File **ohne** webmon-Spawn; **Default ohne Flag byte-identisch** (kein Event-File, keine Callbacks) — bestehende Regressionstests grün; `--run-dir` respektiert.

**Integration / Verhalten:**
- FastAPI `TestClient`: jede Stationen-Route 200 + erwartetes Fragment; Start/Stop/Resume rufen den (Fake-)Launcher korrekt; SSE-Endpoint streamt View-Updates.
- **Rückwärtskompat:** Kern ohne `[gui]` voll funktionsfähig; `ramcheck` ohne `gui`-Befehl unverändert.

**Live-Smoke (manuell, am Ende):** `ramcheck gui` → ndassist-Pack + M5-Config wählen → eval starten; Dashboard zählt live hoch, Stop sauber (kein Zombie), Bundle erscheint; judge-Config wählen → judge starten → Scorecard in Ergebnis-Ansicht; Vergleich zeigt Aggregat; Export lädt `scorecard.md`. **Zusätzlich:** eval stoppen → „Fortsetzen" → Live zeigt **nicht** fälschlich „fertig"; GUI neu starten während Lauf → Discovery zeigt running + zweiter Start verweigert.

## 11 · Scope-Grenze (YAGNI)

**In v1 (Skeleton):** App-Shell + 7 Stationen je in der „v1"-Spalte aus §4; `--run-dir` + `--emit-events` + Event-Writer-Entkopplung + `master_rows`-Helper-Extraktion (§5); Run-Sentinel + Sentinel-Lock (G8); eval+judge start/stop/resume; Tail-Live-Fortschritt; **Ergebnis-Zahlen via `scorecard_mod` in Jinja-Tabellen** (nicht `.md`-Render); Aggregat-Tabelle; Datei-Download-Export.

**Bewusst NICHT in v1** (spätere Specs): **`.md`→HTML-Render** (kein Markdown-Dep; v1 rendert die Zahlen direkt); Config-/Pack-Editor im UI; echte interaktive Plots (Zeitreihe/Verteilung/Histogramm); verlinkter Transparenz-Graph; Filter/Suche/Sortierung; parallele Läufe; in-memory-PID-Recovery jenseits des Sentinels; `resources.jsonl`-Offset-Tailing (v1 nutzt `loadview.latest_load` wie webmon — Voll-Datei-Read akzeptiert); Auth/Multi-User/Remote (bleibt 127.0.0.1-lokal); Live-Token-Stream.

## 12 · Berührte / neue Dateien

| Datei | Änderung |
|---|---|
| `ramcheck/gui/__init__.py` | **neu** — Package-Marker |
| `ramcheck/gui/app.py` | **neu** — FastAPI-App-Factory, Routen der 7 Stationen, static/templates mount |
| `ramcheck/gui/control.py` | **neu** — Lauf-Registry + `ProcessLauncher`-Protocol + run_dir-Wahl + Run-Sentinel + G8-Lock |
| `ramcheck/gui/bundles.py` | **neu** — Discovery/Klassifikation (§8) + Urteil-Recompute (geteilter `master_rows`-Helper) |
| `ramcheck/gui/live.py` | **neu** — Live-View-Adapter (tail + `build_view` → SSE-Fragment), G7 |
| `ramcheck/gui/templates/`, `ramcheck/gui/static/` | **neu** — Jinja2-Fragmente + vendored HTMX/Alpine/CSS (G11) |
| `ramcheck/cli.py` | `gui`-Befehl (lazy import); **`--run-dir`** (eval+judge); **`--emit-events`** + Event-Writer von `_live_monitor` entkoppeln (Default byte-identisch); `_master_rows`-Helper herausfaktorisieren |
| `pyproject.toml` | `[gui]`-optional-dependency-Gruppe (fastapi/uvicorn/jinja2/python-multipart) |
| `tests/` | neue Tests je §10 (Kern ohne FastAPI; GUI-Routen optional/skip ohne Extra) |
| `AGENTS.md` | `ramcheck gui` in Befehlsliste; Architektur-Notizen: GUI = optionales `[gui]`-extra spawnt Mess-Subprozesse mit `--run-dir`/`--emit-events`; `runs/`=SSOT; Run-Sentinel `run.json` (transient, G8-Lock); event-Files non-SSOT; ein Lauf zur Zeit |

## 13 · Offene Detailpunkte (für den Implementierungsplan)

- **Tailwind build-frei:** vendored Utility-CSS vs. Play-CDN vs. handgeschriebenes CSS im Tailwind-Stil — Abwägung Offline-Fähigkeit ↔ Aufwand.
- **uvicorn Auto-Port:** bei `--port 0` den OS-zugewiesenen Port aus dem `uvicorn.Server`-Socket (`server.servers[*].sockets[0].getsockname()`) lesen, bevor `webbrowser.open` — der webmon-Trick (eigenen stdout lesen) geht in-process nicht.
- **SSE-Render-Form:** HTMX-Fragment-Swap (server-rendert HTML) vs. JSON→Alpine (client-rendert) für die Live-Karte — vermutlich HTMX fürs Skelett, Alpine wo Client-State nötig.
- **`master_rows`-Zielmodul:** in `scorecard.py` (zur restlichen Scorecard-Math) vs. eigenes kleines Helper-Modul — so dass weder GUI noch CLI zirkulär importieren.
