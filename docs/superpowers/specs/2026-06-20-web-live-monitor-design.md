# Web Live-Monitor (`touchstone eval --web`) — Design (Ink. 3)

**Datum:** 2026-06-20
**Status:** ratifiziert (Brainstorming abgeschlossen, vor Implementierungsplan)
**Scope:** v1 — read-only Live-Monitor des `eval`-Laufs im Browser.

## 1 · Ziel & Motivation

Ein `eval`-Lauf auf der Maschine ist die lange, RAM-/Throttle-interessante Generierungsphase
(Matrix model × variant × prompt × repeat, jede Zelle ein blockierender `stream_once`). Heute
sieht man währenddessen nur sporadische Konsolen-Zeilen. **Ink. 3** liefert ein **flüchtiges
Live-Fenster** auf einen laufenden Lauf: Fortschritt X/N, ETA, Live-Systemlast (RAM / Memory-
Pressure / Throttle) und Pass/Fail + Latenz pro fertiger Zelle — im Browser, lokal.

Vision (später, nicht v1): Token-/Thinking-Stream, `judge --web`, fertige/alte Läufe anzeigen.

## 2 · Ratifizierte Entscheidungen (mit Begründung)

Alle vier Eingangs-Forks + zwei Architektur-Forks wurden mit dem User ratifiziert; die zwei
Architektur-Forks zusätzlich durch ein adversariales 3-Lens-Red-Team (einstimmig, hohe Konfidenz)
gegen den Quellcode verifiziert.

| # | Entscheidung | Begründung |
|---|---|---|
| D1 | **Web-UI** statt Terminal-TUI | User-Wunsch; passt zu seinem Ökosystem (`podcast-to-youtube`, `whisper-pipeline`). |
| D2 | **Verfassungs-Amendment**: `AGENTS.md` „Markdown+CSV, no HTML" gilt nur für **persistierte Output-Artefakte**. Der Monitor ist **transiente Instrumentierung**, persistiert **kein** HTML. | Bundle bleibt `responses.jsonl`/`perf.csv`/`scorecard.md`/`scores.csv`. Der Browser-View verschwindet beim Schließen. |
| D3 | **Separater Prozess (Modell B)**: Mess-Lauf läuft unverändert im Hauptprozess; der Monitor ist ein **gespawnter Subprozess**, der Dateien tailt. | Red-Team einstimmig. Spiegelt das bestehende `_SamplerProcess`-Muster (`runner.py:270`) und die Verfassung „memory is never estimated from the request thread" (`AGENTS.md:17-18`). Mess-Prozess bleibt **unberührt** → keine Perturbation, future-proof für Token-Streaming. |
| D4 | **stdlib `http.server` + handgerolltes SSE**, **keine** neuen Dependencies, **kein** `[web]`-Extra | User wählte Schlankheit über Haus-Stil-Konsistenz. Repo hat heute null async/web (`grep` leer); ein file-tailing-SSE-Server ist ~120–160 Zeilen stdlib. |
| D5 | **`events.jsonl`** — dedizierter, **append-only** Event-Kanal (nie neu geschrieben), getrennt von `responses.jsonl` | `responses.jsonl` wird beim Finalize komplett neu geschrieben (`qualrun.py:180`) → ein Tailer darauf ist unter Resume/Abbruch **buggy**. `events.jsonl` spiegelt `resources.jsonl` (append + flush). |
| D6 | **Additive Callbacks** auf `run_eval` (Default `None` → Verhalten byte-für-byte unverändert) | Exakt das `on_verdict`-Muster (`judge.py:200,213-214`) und das `sampler=None`-Idiom. **Null async im Mess-Pfad.** |
| D7 | v1 = **read-only Monitor von `eval`** | Kleinste Fläche. Kein Start-Formular/Abort/History; `judge --web` und `run --web` später (gleiche Loop-Form). |
| D8 | **Token-/Thinking-Stream deferred** | Braucht Änderung an `client.py` (einzige engine-aware Datei) + `StreamEvent`-Kontrakt — eine Mess-Korrektheits-Änderung, kein UI-Wiring. Kommt später „gratis" mit. |
| D9 | **Konsole bleibt** (Browser ergänzt) | `--web` ist Opt-in-Garnitur; CI/headless brauchen nie einen Browser. |
| D10 | Live-Last **nur** aus `resources.jsonl`-Tail | Per-Zelle-Ressourcen existieren erst nach Finalize (`qualrun.py:153-156,170-177`); der Sampler-Stream (2 Hz) ist die einzige Live-Quelle. |

**Verworfen — Modell A (in-process):** uvicorn-Event-Loop + SSE im selben Prozess wie die Messung.
Das Kern-Gegenargument (vom Red-Team verifiziert): A ist **nicht billiger**. A's natürliche Live-
Quelle wäre `responses.jsonl`, die beim Finalize neu geschrieben wird → ein Tailer darauf ist
buggy. Um das zu vermeiden braucht *auch A* einen append-only Event-Kanal (= D5, B's einzige echte
Mehrkosten) — und trägt obendrauf asyncio-Thread-Bridge + neue Runtime-Deps. Also **A = B + Extra**.

## 3 · Prozess-Topologie

```
 touchstone eval --pack … --config … --web [--port 0] [--no-open]
 │
 │  (Hauptprozess — Messung, unverändert)
 ├─ run_eval(...)  ──fires──▶ on_run_start / on_cell_start / on_cell_done   (D6)
 │     │                         │
 │     │                         └─▶ Writer-Closure (im CLI): append+flush ▶ events.jsonl   (D5)
 │     │
 │     ├─ spawnt _SamplerProcess  ───────────────────────▶ resources.jsonl   (2 Hz, bestehend)
 │     └─ schreibt responses.jsonl (append, dann Finalize-Rewrite — NICHT getailt)
 │
 └─ spawnt _WebMonitorProcess  (wie _SamplerProcess)
        │  python -m touchstone.webmon --bundle <run_dir> --port <p>
        │  tailt events.jsonl + resources.jsonl, serviert SSE
        └─ druckt gebundenen Port auf stdout ▶ Hauptprozess öffnet Browser (webbrowser.open)
```

Der Monitor-Subprozess kennt den Mess-Prozess **nicht** — er liest nur zwei Dateien. Stirbt der
Monitor, läuft die Messung ungestört weiter (und umgekehrt). Beide werden im `finally` gestoppt.

## 4 · Datenkontrakt: `events.jsonl`

Append-only, eine JSON-Zeile pro Event, **flush nach jedem Write** (Sichtbarkeit). Liegt im
`run_dir` neben `responses.jsonl` (also unter dem gitignorierten `runs/`). Resume: Append-Modus
(`"a"`), prior-Session-Events bleiben erhalten.

```jsonc
{"ts": 1718841600.12, "type": "run_start", "total": 48}
{"ts": 1718841601.06, "type": "cell_start", "i": 0,
 "model": "qwen2.5:3b", "variant": "baseline", "category": "A", "prompt_id": "A1", "repeat": 0}
{"ts": 1718841613.91, "type": "cell_done",  "i": 0,
 "model": "qwen2.5:3b", "variant": "baseline", "prompt_id": "A1", "repeat": 0,
 "ok": true, "ttft_s": 0.31, "e2e_s": 12.8, "decode_tps": 17.2,
 "completion_tokens": 220, "content_empty": false, "error": ""}
{"ts": 1718842050.5, "type": "run_done", "total": 48, "ok": 47}
```

**Dedup-/Fortschritts-Schlüssel = `(model, variant, prompt_id, repeat)`** (== `_cell_key`,
`qualrun.py:65`), **nicht** der Loop-Index `i`. Grund: bei `--resume` startet `i` neu bei 0; der
Key ist über Sessions stabil. Der Monitor zählt distinkte `cell_done`-Keys → korrekter Fortschritt
auch nach Resume. `total` wird pro `run_eval`-Aufruf geschrieben (Monitor nimmt das Maximum).

## 5 · Kern-Änderung: drei optionale Callbacks auf `run_eval`

Einzige Änderung am Mess-Modul `qualrun.py`. Signatur:

```python
def run_eval(
    config, pack, client, *,
    run_dir, sampler=None, settle_s=0.0, resume=False,
    on_run_start:  Callable[[int], None] | None = None,             # total = len(cells)
    on_cell_start: Callable[[int, EvalCell], None] | None = None,    # (i, cell)
    on_cell_done:  Callable[[int, EvalResponse], None] | None = None, # (i, resp)
) -> list[EvalResponse]: ...
```

- `on_run_start(len(cells))` direkt nach `cells = iter_eval_cells(...)` (`qualrun.py:107`). Gibt dem
  UI sofort N und behandelt den Fall „alle Zellen schon erledigt" (kein cell-Event).
- Loop wird `for i, cell in enumerate(cells):`. `on_cell_start(i, cell)` vor `stream_once`
  (`:119`), **nach** der Skip-Prüfung (`:117-118`) — übersprungene Zellen feuern nicht.
- `on_cell_done(i, resp)` direkt nach dem `fh.flush()` (`:165`).

Default `None` → kein Aufruf → **alle 101 Tests bleiben grün**. Sonst keine Änderung am Kern.
`run_done` schreibt das **CLI** in seinem `finally` (nach `run_eval`-Rückkehr, crash-sicher).

Die Writer-Closures (events.jsonl append+flush) leben im **CLI** (`eval_cmd`), nicht in `run_eval` —
`run_eval` bleibt ahnungslos über Web/events.jsonl. Das ist die Naht (wie `on_verdict` im `judge`).

## 6 · Komponenten

```
touchstone/webmon.py        Monitor-Subprozess-Entry (python -m touchstone.webmon).
                          stdlib ThreadingHTTPServer, bind 127.0.0.1:<port>.
                            GET /         → statische HTML-Shell (inline CSS + Vanilla-JS EventSource)
                            GET /events   → text/event-stream: Snapshot-on-connect + Tail-Deltas
  ├─ events.py (pure)     Event-Dataclasses + (de)serialisierung; view-model-Aggregation
  │                       (events[] → progress/ETA/per-cell-table); ETA = Mittel(e2e fertiger) × Rest.
  ├─ tail.py (pure)       tail_new_lines(path, offset) -> (lines, new_offset); toleriert halbe Endzeile.
  └─ loadview.py (pure)   letzter ResourceSample aus resources.jsonl → {sys_used_mb, pressure, throttle}.

runner.py                 + class _WebMonitorProcess  (kopiert _SamplerProcess: Popen/terminate/wait(5)/kill).
                          WICHTIG: KEIN Thread-Fallback (s. §7).

cli.py  eval_cmd          + Option --web / --port / --no-open. Wenn --web:
                            events.jsonl öffnen (append), 3 Writer-Closures bauen,
                            _WebMonitorProcess starten, Port lesen, Browser öffnen,
                            Callbacks an run_eval übergeben, im finally run_done schreiben + Monitor stoppen.
```

Schwere Logik in **pure** Modulen (`events`/`tail`/`loadview`) → ohne Server testbar. Der HTTP-
Handler ist dünner Glue. (`webmon.py`, `events.py` etc. unter `touchstone/` oder `touchstone/web/` — im
Implementierungsplan festzulegen; Default: flach in `touchstone/`, konsistent mit der Modul-Ebene.)

## 7 · Fehlerbehandlung & Kanten

- **Monitor-Spawn schlägt fehl** → `console.print` Warnung, Lauf läuft normal weiter. **Kein**
  Thread-Fallback (anders als `_SamplerProcess`): ein In-Process-Fallback würde die Entkopplung
  zerstören, die D3 erst begründet.
- **Port:** Default `--port 0` (OS-vergeben) → Subprozess druckt gebundenen Port auf stdout →
  Hauptprozess liest ihn und öffnet den Browser. Vermeidet Kollision mit `podcast`/`whisper` (8765).
  Expliziter `--port N` möglich; belegt → klare Fehlermeldung.
- **Startup-Reihenfolge:** Der Monitor wird **vor** `run_eval` gestartet; `events.jsonl` ist da
  schon offen (leer), aber `resources.jsonl` legt erst der Sampler **innerhalb** von `run_eval` an.
  → `tail`/`loadview` müssen eine **noch nicht existierende** Datei tolerieren (leerer Stand, bis
  sie auftaucht), nicht nur eine leere.
- **Halbe Endzeile** in events/resources.jsonl (in-flight append) → `JSONDecodeError` abfangen,
  Zeile überspringen, nächster Poll-Tick (~250 ms).
- **Finalize-Rewrite** von `responses.jsonl` ist für den Monitor **irrelevant** — er tailt es nie.
- **Resume:** events.jsonl append; Fortschritt per Key dedupliziert (§4). Sampler/`resources.jsonl`
  startet pro Lauf frisch (bestehendes Verhalten) → Live-Last deckt nur die resumte Portion ab
  (akzeptiert, dokumentiert).
- **Throttle ehrlich:** `resources.jsonl.throttled` ist bool und kann „kein Throttle" *oder*
  „kein passwortloses sudo für powermetrics" heißen. v1 zeigt Throttle, mit statischem Hinweis
  „Throttle erfordert sudo" wenn über den Lauf nie throttled beobachtet. (Sauberere Quell-Lösung —
  ein `throttle_source`-Feld im Sampler — ist eine eigene spätere Änderung, nicht v1.)
- **Browser nie geöffnet / spät verbunden:** Snapshot-on-connect (Handler liest events.jsonl von 0)
  → kein Logfile-Replay-Protokoll nötig; ein spät verbundener Browser sieht den vollen Stand.
- **Lauf-Ende:** Monitor bleibt nach `run_done` stehen (Endzustand lesbar); `Ctrl-C` beendet
  Haupt­prozess, dessen `finally` den Monitor-Subprozess `stop()`t.

## 8 · Testplan (TDD, keine neuen Deps)

- **pure:** `events` (Serialisierung; view-model-Aggregation inkl. ETA + Resume-Dedup-per-Key);
  `tail` (Temp-Datei inkrementell wachsen lassen, halbe Endzeile toleriert, Offset-Fortschritt);
  `loadview` (letzter Sample, leere/partielle/**nicht existierende** Datei).
- **Callbacks:** `test_qualrun` erweitern — mit DI-Fake-Client/Sampler feuern `on_run_start`/
  `on_cell_start`/`on_cell_done` mit korrekten Argumenten; Default-ohne-Callbacks **byte-identisch**
  (Regression gegen die 101 grünen Tests).
- **Server-Integration:** `ThreadingHTTPServer` auf Port 0 starten, mit `http.client` `GET /` (200,
  rendert) und `GET /events` gegen eine Temp-`events.jsonl` (Snapshot + ein nachträglich angehängtes
  Event wird als SSE geliefert).
- Läuft in der bestehenden CI (GitHub Actions) ohne Zusatz-Setup, da reine stdlib.

## 9 · `AGENTS.md`-Amendment (Teil der Umsetzung)

- Zeile „Output is Markdown + CSV, nothing else. No HTML." um den Zusatz ergänzen, dass dies
  **persistierte Output-Artefakte** meint; der `--web`-Monitor ist transiente Instrumentierung und
  persistiert kein HTML (D2).
- `cli.py`-Zeile in der Architektur-Liste: `eval … --web` ergänzen.
- Neues Artefakt `events.jsonl` (append-only) + `_WebMonitorProcess` (decoupled, wie Sampler)
  dokumentieren; Gotcha „Monitor tailt nie `responses.jsonl` (Finalize-Rewrite)" ergänzen.

## 10 · Explizit außerhalb v1

Token-/Thinking-Stream · `judge --web` · `run --web` · Start-Formular / Abort / Run-History /
`serve <bundle>` für fertige Läufe · Jinja2/HTMX/FastAPI · per-Zelle-Live-RAM (existiert erst nach
Finalize) · `throttle_source`-Feld im Sampler.

## 11 · Datei-Manifest (neu/geändert)

- **neu:** `touchstone/webmon.py`, `touchstone/events.py`, `touchstone/tail.py`, `touchstone/loadview.py`
  (oder gebündelt unter `touchstone/web/` — Plan-Entscheidung)
- **neu:** statische Monitor-Shell (HTML/CSS/JS — inline in `webmon.py` oder `touchstone/web/static/`)
- **geändert:** `touchstone/qualrun.py` (3 optionale Callbacks), `touchstone/runner.py`
  (`_WebMonitorProcess`), `touchstone/cli.py` (`eval_cmd`: `--web`/`--port`/`--no-open` + Wiring)
- **geändert:** `AGENTS.md` (§9), Tests unter `tests/`
- **unverändert:** keine `pyproject.toml`-Deps (stdlib-only)
