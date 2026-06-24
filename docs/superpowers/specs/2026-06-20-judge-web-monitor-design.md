# Judge Live-Monitor (`touchstone judge --web`) — Design (Ink. 5)

**Datum:** 2026-06-20
**Status:** ratifiziert (Brainstorming abgeschlossen, vor Implementierungsplan)
**Scope:** v1 — read-only Live-Monitor des `judge`-Laufs im Browser.
**Baut auf:** [`2026-06-20-web-live-monitor-design.md`](2026-06-20-web-live-monitor-design.md) (Ink. 3, `eval --web`).

## 1 · Ziel & Motivation

Ein `judge`-Lauf bewertet jede Antwort eines Bundles mit einem LLM-Judge (ein Call pro
Antwort, ~15 s gegen einen lokalen 35B-Judge). Über 24+ Prompts × Modelle × Varianten ist
das eine lange Phase, in der man heute nur sporadische Konsolen-Zeilen sieht. **Ink. 5**
liefert ein **flüchtiges Live-Fenster** auf einen laufenden Bewertungslauf — analog zu
`eval --web` (Ink. 3), aber **scorend statt latenz-zentriert**:

- Fortschritt X/N + ETA
- Live-**Score-Verteilung** (Histogramm 1–5) + **Red-Flag-Zähler** + Ø-Score
- **Verdict-Tabelle** (Prompt · Modell·Variante · Score · Red? · Begründung)
- am Lauf-Ende die **gewichtete Master-Scorecard-Vorschau** (%, Safety-K.-o., Empfehlung)

Es ist der „eigene Judge-View", der in der Ink.-3-Spec als *deferred* notiert wurde
(„Verdicts = Score/Red-Flag-Verteilung, **keine** Latenz → passt nicht ins zell-/latenz-
geformte Dashboard").

## 2 · Ratifizierte Entscheidungen (mit Begründung)

Zwei Forks wurden mit dem User ratifiziert; der Rest ist von `eval --web` (Ink. 3, D1–D10)
präzedenzfest übernommen und nicht neu verhandelt.

| # | Entscheidung | Begründung |
|---|---|---|
| **J1** | **Dashboard „Voll"**: zusätzlich zur Verdict-Live-Sicht am Lauf-Ende die **Master-Scorecard-Vorschau** (%, Safety, Empfehlung) | Das ist das eigentliche Geld-Resultat — man sieht das Urteil, ohne `scorecard.md` zu öffnen. (User-Fork 1) |
| **J2** | **`webmon.py` generalisieren** (Fork A): webmon wird reiner Transport (SSE + tail + serve + Port-Print); der konkrete **View** (HTML + Aggregation + getailte Streams) kommt aus einem View-Modul, gewählt per `--view eval\|judge` | Ein Fernseher, zwei Sender. Die fehleranfällige Klempnerei (SSE/Lifecycle — Quelle von 6 Review-Bugs in Ink. 3) existiert **einmal**, wird **einmal** gefixt. Kein dupliziertes Server-Gerüst. Passt zu D4 (schlank) + „kleine, fokussierte Units". (User-Fork 2) |
| J3 (von D5) | **`judge_events.jsonl`** — dedizierter, **append-only** Event-Kanal, getrennt von `judgements.jsonl` | `judgements.jsonl` wird beim Resume-Finalize komplett neu geschrieben (clean rewrite, `cli.py:482`) → ein Tailer darauf wäre buggy. `judge_events.jsonl` ist append+flush, wie `events.jsonl`/`resources.jsonl`. |
| J4 (von D6) | **Additive Callbacks** im Judge-Pfad (Default `None` → `judge` ohne `--web` byte-für-byte unverändert) | `judge_bundle` feuert bereits `on_verdict(v)` (`judge.py:201`). Start/Master/Done kommen additiv dazu. **Null Verhaltensänderung im Default-Pfad.** |
| J5 | **Kein Load-Panel** (RAM/Memory-Pressure/Throttle) im judge-View | Der Judge läuft auf einem **anderen** Endpoint (LM Studio :1234 / Cloud) — er belastet die Messmaschine nicht repräsentativ. Die `resources.jsonl` im Bundle stammt vom **alten `eval`-Lauf** → wäre irreführend. Der judge-View tailt sie nicht. |
| J6 | **Master-%-/Safety-Math bleibt im Hauptprozess** — der CLI rechnet `weighted_total`/`passes_ko`/`_recommendation` und schreibt das **fertige** Ergebnis ins `master`-Event | Der Monitor-Subprozess hat keinen `Pack` (er tailt nur Dateien) und soll keinen bekommen. Monitor bleibt **reine Anzeige**; die Pack-Math verlässt nie den Hauptprozess. Spiegelt „memory is never estimated from the request thread". |
| J7 (von D7) | v1 = **read-only Monitor von `judge`** | Kleinste Fläche. Kein Start-Formular/Abort/History. |
| J8 (von D9) | **Konsole bleibt** (Browser ergänzt) | `--web` ist Opt-in-Garnitur; CI/headless brauchen nie einen Browser. |

## 3 · Prozess-Topologie

```
 touchstone judge --bundle … --judge-config … --web [--port 0] [--no-open]
 │
 │  (Hauptprozess — Bewertung, im Kern unverändert)
 ├─ on_judge_start(total, prior)  ──▶ Writer-Closure (im CLI):
 │     │                               append judge_start-Event + die prior Verdicts
 │     │                               (resume) als verdict-Events ▶ judge_events.jsonl   (J3)
 │     │
 ├─ judge_bundle(…, on_verdict=…)  ──▶ pro frischem Verdict:
 │     │                               append judgements.jsonl  UND  verdict-Event       (J4)
 │     │
 ├─ nach Rückkehr: pro (Modell·Variante) % + Safety + Empfehlung im Hauptprozess
 │     rechnen (scorecard_mod.weighted_total / passes_ko / _recommendation)
 │     ──▶ master-Event je Gruppe + judge_done-Event                                     (J6)
 │
 └─ spawnt _WebMonitorProcess  (wie eval, events_name="judge_events.jsonl", view="judge")
        │  python -m touchstone.webmon --bundle <dir> --port <p> --events judge_events.jsonl --view judge
        │  tailt judge_events.jsonl (NICHT resources.jsonl — J5), serviert SSE
        └─ druckt gebundenen Port auf stdout ▶ Hauptprozess öffnet Browser
```

## 4 · Modul-Struktur (Fork A / J2)

```
webmon.py        Transport: SSE-Server + tail + Port-Print + View-Dispatch (--view)
  ├─ events.py        eval-View:  INDEX_HTML + build_view (Latenz/RAM)   ← eval-HTML zieht
  │                                                                        aus webmon hierher
  └─ judge_events.py  judge-View: INDEX_HTML + build_view (Scores)       ← NEU
```

Ein **View** ist ein Modul mit dreierlei:
- `INDEX_HTML: str` — die gerenderte Dashboard-Seite (+ SSE-Client-JS)
- `build_view(events: Iterable[dict]) -> RunView`-artig mit `.as_dict()` — faltet den
  Event-Strom in ein render-fertiges View-Model
- `TAILS_RESOURCES: bool` — ob der Server zusätzlich `resources.jsonl` für ein `load`-Event
  tailt (eval: `True`, judge: `False`)

`webmon.make_handler(bundle, events_name, view)` wählt per `view` das Modul; `webmon.main`
nimmt `--view eval|judge` (Default `eval`). Das eval-`INDEX_HTML` wandert aus `webmon.py`
nach `events.py` — webmon.py wird dadurch **fokussierter** (nur Transport), nicht größer.

## 5 · Event-Kontrakt (`judge_events.jsonl`)

Parallel zu `events.py`. Pure (de)serialisierung, kein I/O.

| Event | Felder | Wann |
|---|---|---|
| `judge_start` | `ts, total` | einmal zu Beginn; `total` = Anzahl zu bewertender Antworten |
| `verdict` | `ts, i, model, variant, prompt_id, repeat, category, score, red_flag, unscored, rationale` | pro Verdict (prior beim Resume vorab; danach frische via `on_verdict`); `rationale` auf ~160 Zeichen gekürzt; `score` 0 wenn `unscored` |
| `master` | `ts, model, variant, pct, safety_passed, safety_reason, recommendation` | eines pro (Modell·Variante), am Ende — schon fertig gerechnet (J6) |
| `judge_done` | `ts, total, scored` | im `finally`, auch nach Abbruch |

`build_view` faltet das zu:
- `total`, `done` (= Anzahl Verdicts), `eta_s` (Ø Δt zwischen verdict-Events × Rest)
- `histogram` = Counts je Score 1..5; `red_flags` = Σ red_flag (scored); `mean_score`
- `verdicts` = Liste (dedup per Key `(model,variant,prompt_id,repeat)`, last wins → resume-fest)
- `masters` = Liste der master-Zeilen; `finished`

Defensiv wie `events.parse_line`: leere/halbe/kaputte Zeilen → `None` (toleriert eine
halb-geschriebene Schlusszeile bei laufendem Tail).

## 6 · Callback-Verdrahtung im CLI

Im `judge`-Command (analog zu `_eval_event_writers`, neu `_judge_event_writers`):

- **ohne `--web`** (Default): unverändert — `judge_bundle(..., on_verdict=_append)` schreibt
  nur `judgements.jsonl`. Kein `judge_events.jsonl`, keine neuen Callbacks.
- **mit `--web`**: `with _live_monitor(bundle, port, no_open, events_name="judge_events.jsonl",
  view="judge")`. `on_verdict` wird zu „append `judgements.jsonl` **und** verdict-Event".
  `on_judge_start(total, prior)` schreibt Start + prior-Verdicts. Nach `judge_bundle`-Rückkehr
  rechnet der CLI die master-Zeilen und schreibt sie + `judge_done` (Letzteres im `finally`).

`_live_monitor` / `_hold_monitor` / `_WebMonitorProcess` bekommen einen additiven
`view`-Parameter (Default `"eval"`) — sonst unverändert.

## 7 · Error-Handling

Wie eval (Ink. 3): Monitor-Crash kann den Bewertungslauf **nie** umbringen (entkoppelter
Subprozess); SSE bricht sauber bei Browser-Close (`BrokenPipeError`/`OSError` → Stream endet,
Thread lebt); `judge_events.jsonl` append+flush, halbe Schlusszeile beim Parsen toleriert;
`judge_done` im `finally`, damit das Dashboard auch nach Abbruch „fertig" zeigt. Ohne `--web`:
kein `judge_events.jsonl`, kein Callback — Default-Pfad byte-identisch.

## 8 · Teststrategie (TDD)

**Unit (pure):**
- `judge_events`: Event-Konstruktoren round-trip; `parse_line` (leer/halb/kaputt → `None`);
  `build_view` (Histogramm, mean, Red-Count, ETA, Master-Faltung, **Key-Dedup über
  Resume-Replay**).
- CLI-Writer-Closures (`_judge_event_writers`): prior → verdict-Events; `on_verdict` →
  Event **und** `judgements.jsonl`; master/done korrekt.

**Integration / Verhalten:**
- `webmon` View-Dispatch: `--view judge` liefert judge-HTML; `--view eval` byte-identisch
  zu heute (Regression-Guard nach HTML-Umzug).
- **Rückwärtskompat:** `judge` ohne `--web` schreibt **kein** `judge_events.jsonl` und ruft
  keine neuen Callbacks (Default-Pfad byte-identisch).

**Live-Smoke (manuell, am Ende):** ein Bundle (z. B. `runs/2026-06-20_104844_eval_ndassist`,
`judgements.jsonl` gelöscht) gegen LM Studio :1234 `qwen3.6-35b` mit `--web` re-judgen;
prüfen: Dashboard zählt live hoch, Histogramm/Red-Flags füllen sich, Master-Vorschau
erscheint am Ende, Ctrl-C-Shutdown sauber (keine Zombies).

## 9 · Scope-Grenze (YAGNI)

v1 = read-only Monitor von `judge`. **Nicht** in v1: Start-Formular, Abort-Knopf, History/alte
Läufe, per-Verdict-Latenz (uninteressant — anderer Endpoint), Live-Token-Stream des Judge.

## 10 · Berührte Dateien

| Datei | Änderung |
|---|---|
| `touchstone/judge_events.py` | **neu** — judge-View: Event-Konstruktoren + `parse_line` + `build_view` + `INDEX_HTML` + `TAILS_RESOURCES=False` |
| `touchstone/events.py` | eval-`INDEX_HTML` von webmon hierher ziehen; `TAILS_RESOURCES=True` ergänzen |
| `touchstone/webmon.py` | View-Dispatch (`--view`), HTML aus dem Modul statt hartkodiert, `resources.jsonl` nur tailen wenn `TAILS_RESOURCES` |
| `touchstone/runner.py` | `_WebMonitorProcess`: additiver `view`-Parameter → an `webmon`-Subprozess durchreichen |
| `touchstone/cli.py` | `_live_monitor`/`_hold_monitor`: `view`-Parameter; neu `_judge_event_writers`; `judge`-Command um `--web/--port/--no-open` + Verdrahtung |
| `tests/` | neue Tests je Abschnitt 8 |
| `AGENTS.md` | `judge --web` in Befehlsliste + ggf. Gotcha (judge-View tailt nie `resources.jsonl`) |
