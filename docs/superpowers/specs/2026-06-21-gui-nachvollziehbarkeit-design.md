# GUI als Labor — Phase 1: Nachvollziehbarkeit (`ramcheck gui`) — Design (Ink. 7)

**Datum:** 2026-06-21
**Status:** ratifiziert (Brainstorming abgeschlossen) + nach adversarialer Code-Review geschärft — vor Implementierungsplan
**Scope:** Phase 1 von zwei — „das Labor **sehen & verstehen**": die Read-/Präsentationsschicht der GUI von „Schlagwörtern ohne Kontext" zu **lückenlos nachvollziehbaren, verknüpften Ansichten** umbauen, plus die Erfassungs-Lücke schließen, die volle Nachvollziehbarkeit heute verhindert.
**Baut auf:** [`2026-06-21-gui-steuerzentrale-design.md`](2026-06-21-gui-steuerzentrale-design.md) (Ink. 6) — die Architektur (out-of-process Control-Plane, Run-Sentinel, Sicherheit, Daten-Layer) bleibt; Phase 1 ersetzt vor allem die Präsentation.
**Phase 2 (eigene Spec, später):** „das Labor **bedienen**" — Variablen entkoppeln, im Tool editieren, kontrolliert vergleichen (eine Achse variiert, inkl. Hardware-Vergleich gleicher Seed).

## 1 · Ziel & Motivation

Die Ink.-6-GUI war ein Walking Skeleton: technisch lauffähig, aber als **Produkt unbrauchbar** — die Ansichten zeigen Schlagwörter und nackte Zahlen („ndassist", „Nein", „29%") ohne den Kontext, der sie bedeutet. Damit verfehlt sie genau die **eine Anforderung, die das WebUI rechtfertigt**: Transparenz/Nachvollziehbarkeit.

Der Zweck (im Brainstorming geschärft): **`ramcheck` ist ein Labor.** Es dient dazu, **falsifizierbare Aussagen** der Form „Modell X auf Hardware Y mit Settings Z und Prompt A taugt für Aufgabe B" zu bilden und zu prüfen — durch kontrollierte Variation einzelner Variablen. Die Arbeitsschleife ist **sehen → nachvollziehen → ändern → erneut prüfen**.

Phase 1 baut das **Fundament dieser Schleife**: *sehen & nachvollziehen*. „Nein" muss lückenlos durchklickbar sein bis zur Wurzel. **Wichtig (Review-Kernpunkt):** die Master-Dimensionen werden **holistisch** bewertet (ein Judge-Call über *alle* Antworten), es gibt **keine** strukturelle Dimension→Prompt-Zuordnung im Pack. Die Nachvollziehbarkeit einer Dimension läuft deshalb **über ihre Begründung** (die der Judge mit konkreten prompt_id-Belegen liefert), nicht über eine erfundene per-Aufgabe-Verlinkung (siehe L8).

## 2 · Ratifizierte Entscheidungen (mit Begründung)

| # | Entscheidung | Begründung |
|---|---|---|
| **L1** | **`ramcheck` ist ein Labor, kein Dashboard.** Erstklassige Objekte sind perspektivisch **Variablen** (Hardware · Modell · Kontext-Window · Sampling · System-Prompt · Input-Material · Bewertungskriterien · Bewertungsmodell). | Zweck: nachvollziehbare, falsifizierbare Aussagen entwickeln und prüfen — jede Variable einzeln. |
| **L2** | **Nachvollziehbarkeit zuerst (Phase 1), Variablen-Entkopplung + Bedienung danach (Phase 2).** | Reihenfolge der Schleife; löst den akuten Schmerz; geringes Wegwerf-Risiko (Read liest die Ausgangs-, Entkopplung die Eingangs-Seite); Verständnis schärft erst das Entkopplungs-Design. |
| **L3** | **Drei verknüpfte Ansichten** (Ergebnis · Kriterien · Übersicht), Leitprinzip **Drill-down + keine Sackgassen**. | Eine kohärente App, ein Fluss — gegen die „Dashboard, das man verlassen muss"-Kritik. |
| **L4** | **`dim_rationales` erfassen + `reports.jsonl` als `ModelReport`-Persistenz.** Konkretes Judge-JSON-Schema + atomarer Batch-Write (siehe §4.1). | Ohne Per-Dimension-Begründung ist „Warum Q6 = 2?" unbeantwortbar (heute `dim_rationales={}`, `judge.py:224`). `reports.jsonl` schließt zugleich die `ModelReport`-Persistenz-Lücke und **löst die fragile `scores.csv`-Rekonstruktion ab** (Ink.-6-Blocker). |
| **L5** | **CPU-Last ins Sampling** (`resources.jsonl`), für den RAM-**und**-CPU-Verlauf. Additiv, berührt `RAW_CSV_COLUMNS` nicht. **Bewusster „Rider"**, nicht Kern-These (§9). | Johannes nannte CPU explizit als Verlaufs-Messgröße; der Sampler erfasst heute nur RAM/Pressure/Throttle. |
| **L6** | **Fundament bleibt; Phase 1 überarbeitet die Read-/Präsentationsschicht.** | Die in Ink. 6 adversarial gehärtete Architektur ist solide; geändert wird, was unbrauchbar war. |
| **L7** | **Steuerung & Vergleich bleiben funktional, werden NICHT in Phase 1 brauchbar gemacht.** | YAGNI; gehören zur „Bedienung" (Phase 2). |
| **L8** | **Dimensions-Nachvollziehbarkeit via holistische Begründung mit prompt_id-Belegen — nicht via (nicht-existenter) Dimension→Aufgabe-Struktur.** Der Judge nennt in jeder Dimensions-Begründung 1–2 prompt_ids als Beleg; die UI macht diese Zitate klickbar. | Master-Dimensionen sind holistisch (`score_dimensions` = ein Call über alle Antworten, `judge.py:121`); der Pack trägt keine Dimension→Prompt-Kante (nur `ko_rule.dimension`/`red_flag_prompts`). Eine erfundene Zuordnung („alle Antworten" / „Kategorie") wäre Schlagwort-ohne-Kontext eine Ebene tiefer. Die **Begründung selbst** ist die ehrliche Evidenz-Brücke. |
| **L9** | **Die Bewertungs-*Methode* ist im UI abrufbar erklärt UND dauerhaft dokumentiert** — nicht nur die Daten, sondern *wie* bewertet wird: holistische Dimensionen · gewichtete Master-Scorecard (Σ Score×Gewicht / Max) · die zwei K.-o.-Zweige · die belegte Begründung als Evidenz-Brücke · die 1–5-Scale. Als abrufbares Erklär-Element in der Kriterien-/Ergebnis-Ansicht **und** in `docs/explanation/`. Eine Quelle, zwei Orte. | Johannes' #1-Anforderung ist „was nach welchen Kriterien **wie** getestet wird" — das *Wie* ist die Methode. Ohne sie bleiben die Zahlen Schlagwörter; sie ist so wichtig für Nachvollziehbarkeit, dass sie reproduzierbar (Doku) **und** im Werkzeug selbst (UI) verfügbar sein muss. |

## 3 · Die drei Ansichten

### 3.1 Ergebnis-Ansicht (das Herz) — `/result/{run}`

Der Drill-down eines Laufs (Modell·Variante·Hardware·Seed), lückenlos von oben nach unten (Mockup ratifiziert):

1. **Kopf — scharfe Aussage + Urteil.** Modell · System-Prompt-Variante · Hardware · Seed; die Aufgabe in Klartext; das Urteil als Badge mit **Grund** + Qualität-%.
2. **Herleitung.**
   - **(a) K.-o.-Regel mit dem *aktiven Zweig*.** `passes_ko` schlägt aus **zwei** unabhängigen Gründen fehl — die Box zeigt explizit, **welcher** feuerte, und verlinkt zur **echten Wurzel**:
     - *Dimensions-Floor* (z. B. Q6 ≤ 2) → die **holistische `dim_rationale`** der Dimension (mit ihren zitierten prompt_id-Belegen, L8).
     - *Red-Flag-Prompt* (z. B. E1 red-flagged) → das **per-Antwort-Verdict + `rationale`** genau dieses Prompts (existiert bereits).
   - **(b) Gewichtete Master-Scorecard.** Jede Dimension `name × Gewicht → Score`, K.-o.-Dimension hervorgehoben, Σ/Max = %. Jede Dimensionszeile expandiert zu ihrer **holistischen Begründung** (`dim_rationale`); die darin zitierten prompt_ids sind **klickbare Links** zur jeweiligen Antwort (3.). **Keine** Behauptung „dies sind *die* prüfenden Aufgaben".
3. **Antworten, nach Kategorie** (die natürliche Pack-Struktur A–E — *das* ist die „alle Aufgaben durchblättern"-Navigation, getrennt von der Dimensions-Herleitung). Je Prompt aufklappbar: die Aufgabe (Input-Text), Green/Red-Flags, die **vollständige Modell-Antwort**, das per-Antwort-Verdict (Score, Red-Flag, `rationale`). Reasoning-only-Antworten (`content_empty` + `reasoning_chars>0`) markiert. Sprungziel der prompt_id-Links aus 2(b).
4. **Perf + Ressourcen-Verlauf.** TTFT P50/P95, Decode-Median; **RAM- und CPU-Verlauf** mit **Max + Ø** (aus `resources.jsonl`), Throttle/Akku-Flag.

**Empty-States (nie blank/500):** un-judged / eval-only Bundle → die Master-Scorecard-Sektion zeigt explizit „noch nicht bewertet — `ramcheck judge` ausführen" (spiegelt den bestehenden `scorecard.md`-Zweig). `dim_rationale` aus dem `scores.csv`-Fallback fehlt → „Begründung nicht erfasst" statt Lüge.

### 3.2 Kriterien-Ansicht „was prüft dieser Test" — `/packs/{pack}`

Der Pack als selbsterklärende Referenz: scharfe Aufgaben-Beschreibung; **1–5-Scale-Legende**; gewichtete Dimensionen (`id · name · ×Gewicht · about`); K.-o.-Regel prominent (Dimension + Schwelle + `red_flag_prompts`); System-Prompt-Varianten mit vollem Text; pro Kategorie die Prompts (Text · `tests` · Green/Red-Flags · `safety_critical`/`format_strict`/`repeats`). Jede Aufgabe rückverlinkt zu ihren Antworten in den Läufen.

**Bewertungs-Methode erklärt (abrufbar, L9).** Ein erklärendes Element (Panel/aufklappbar, in Kriterien- und Ergebnis-Ansicht erreichbar) macht die *Mechanik* verständlich — nicht nur was, sondern **wie** bewertet wird: dass die Master-Dimensionen **holistisch über alle Antworten** bewertet werden (nicht pro Prompt), wie die **gewichtete Master-Scorecard** rechnet (Σ Score×Gewicht / Max = %), wie die **K.-o.-Logik** mit ihren zwei Zweigen (Dimensions-Floor *oder* Red-Flag-Prompt) ein „Nein" erzwingt, und wie die **belegte Begründung** (klickbare prompt_id-Zitate) die holistische Bewertung rückverfolgbar macht. Der Text ist **dieselbe Quelle** wie der `docs/explanation/`-Abschnitt (kein Drift).

### 3.3 Übersicht — `/` (bedeutungstragender Einstieg)

Pro Lauf eine aussagekräftige Zeile: Modell·Variante · Pack **in Klartext** · Hardware · **Urteil-Badge mit Grund** · Qualität-% · Status (running/judged/eval-only/crashed) · Datum. Filter-/sortierbar nach Variablen (reine Anzeige; Vergleich = Phase 2). Klick → Ergebnis.

## 4 · Datenänderungen

### 4.1 `dim_rationales` + `reports.jsonl` (L4/L8) — bewusste Shape-Änderung, kein Drop-in

Heute ist die Dimensions-Bewertung ein **flaches** `{Q1:<int>}`-Schema (`_build_dimension_prompt` + `parse_dimension_scores`, `judge.py:121-140,78-89`); `score_dimensions` hardcodet `dim_rationales={}` (`judge.py:224`). Rationales erfordern eine **koordinierte Änderung an drei Stellen + zwei Tests**:

- **(a) Judge-Prompt** verlangt das **verschachtelte** Schema und die **Beleg-Pflicht** (L8):
  `{"Q1": {"score": <1-5>, "rationale": "<1 Satz, der 1–2 prompt_ids als Beleg nennt>"}, …}`.
  Damit die Begründung kein erneutes Schlagwort wird, **erweitert die Evidenz im Prompt** sich um genug Kontext, dass der Judge konkrete prompt_ids nennen kann (heute speist `_build_dimension_prompt` nur `prompt_id: score`-Zeilen — das genügt, um prompt_ids zu zitieren; der Vertrag verlangt mindestens den/die ausschlaggebenden prompt_id(s)).
- **(b) Parser:** neuer `parse_dimension_report(raw, pack) -> tuple[dict[str,int], dict[str,str]]` (Scores + Rationales); **tolerant** gegen einen blanken `int` am dim_id (Judge-Drift/Alt-Verhalten → `rationale=""`). `parse_dimension_scores` bleibt als dünner Wrapper (nur Scores) für Bestands-Aufrufer.
- **(c) `score_dimensions`** füllt `dim_rationales` aus dem neuen Parser.
- **Tests:** `test_judge.py:119-122` und `:156-161` pinnen das alte flache Schema → **gehören zur Change-Surface** (FakeBackend-Payloads aufs verschachtelte Schema heben + Rationale-Assertions + „blanker int parst, rationale leer"-Regression). Zusätzlich: der **Beleg-Vertrag** wird getestet (die K.-o.-Dimensions-`rationale` nennt mindestens den K.-o.-relevanten prompt).

**Persistenz `reports.jsonl`** — **ein atomarer Batch-Write am Finalize, kein Append-Stream.** Reports entstehen als fertige Liste *nach* `judge_bundle` (es gibt keinen per-report-Stream wie `on_verdict`). Daher: in **beiden** Judge-Pfaden (`cli.py` default `if not emit:` und emit/web) **ein** sauberes Überschreiben mit der zurückgegebenen Reports-Liste (bei Resume werden Reports ohnehin aus prior+fresh Verdicts neu berechnet). Writer nutzt das **bestehende** `ModelReport.as_dict()` (`results.py:88`); Loader spiegelt `load_judgements_jsonl` (`judge.py:260`) — ~10 Zeilen, halb-geschriebene Schlusszeile tolerant. Zeilen-Schema: `{model, variant, dim_scores:{dim_id:int}, dim_rationales:{dim_id:str}}`.

**Read + Backward-Compat:** `bundles.py` liest `ModelReport` **bevorzugt aus `reports.jsonl`**; fehlt es (Alt-Bundle) → Fallback auf die bestehende `scores.csv`-Rekonstruktion (`dim_rationales={}` → UI „Begründung nicht erfasst"). `scores.csv` hat **keine** Rationale-Spalte → `reports.jsonl` ist die **einzige** Rationale-Quelle (kein Migrationspfad für Alt-Bundles; akzeptiert). `scores.csv` selbst bleibt unverändert (Cross-Run-Aggregat hängt dran).

### 4.2 CPU-Sampling (L5)

- `ResourceSample` bekommt **als letztes Feld** `cpu_pct: float | None = None` (non-kw-only dataclass; Default nötig, damit Alt-`resources.jsonl`-Ticks via `ResourceSample(**d)` laden). **`None` = keine Daten** (die UI lässt die CPU-Spur weg — *keine* falsche 0%-Linie).
- Der Sampler füllt es via `psutil.cpu_percent(interval=None)`; **primen** in `HostSampler.start()` (der erste intervallfreie Call liefert sonst 0.0).
- Gemergte Aggregate (`RAW_CSV_COLUMNS`, `merge.py`-Rollup) **unberührt** in Phase 1 (CPU-Aggregat ist Phase-2-Detail). Der `RAW_CSV_COLUMNS`-Guard prüft nur `RunRecord`, nicht `ResourceSample` (`models.py:126-133`) — additiv sicher.

## 5 · Read-/Render-Schicht

Wiederverwendet (verifiziert vorhanden): `load_pack`, `qualrun.load_responses_jsonl`, `judge.load_judgements_jsonl`, `merge.load_samples_jsonl`, `scorecard.weighted_total/passes_ko/recommendation/mean_score/category_averages/master_rows`, `ModelReport.as_dict`.

Neu/überarbeitet:
- `ramcheck/reports.py` *(oder in `judge.py`)*: `reports.jsonl`-Loader (Writer nutzt `as_dict`).
- `bundles.py`: reichhaltiges Detail-Objekt je Lauf — verknüpft Pack + Antworten + Verdicts + Reports (aus `reports.jsonl`, Fallback `scores.csv`) + Ressourcen-Spur; bedeutungstragende Übersichts-Zeile. Parst prompt_id-Zitate aus `dim_rationales` für die Links.
- `app.py` Read-Routen liefern die reichen Strukturen + Verlinkungs-Anker (Dimension↔zitierte Prompts, Aufgabe↔Antwort).
- Templates `{result,pack,overview}.html` komplett überarbeitet; leichtes RAM/CPU-Verlaufs-Chart (Inline-SVG/Canvas, build-frei).

## 6 · Navigation / Kohärenz (L3)

Ein durchgehender Verlinkungs-Graph, **keine Sackgassen**:
`Übersicht → Ergebnis → { K.-o.-Box → aktive Wurzel (Q-`dim_rationale` *oder* E1-Antwort) · Dimensionszeile → `dim_rationale` → zitierte prompt_ids → Antwort } ↔ Kriterien-Ansicht ↔ zurück`.
Die Antworten-nach-Kategorie-Liste (3.3 Schritt 3) ist die vollständige Durchsicht; aus jeder Aufgabe der Kriterien-Ansicht zu ihren konkreten Antworten. Steuerung/Vergleich bleiben sichtbar, als Phase-2-Platzhalter markiert (kein toter Klick).

## 7 · Error-Handling

- **Alt-Bundle ohne `reports.jsonl`/`cpu_pct`:** Fallback (Rekonstruktion bzw. weggelassene CPU-Spur), nie Crash — die Ink.-6-Regel „ein korruptes Bundle 500t nie die ganze Seite" bleibt.
- **Un-judged / eval-only:** Master-Scorecard-Sektion zeigt „noch nicht bewertet — `ramcheck judge` ausführen" (kein Blank/500). Teil-gejudgte/partielle Dimensionen: fehlende Scores überspringen, partiellen Report rendern.
- **Fehlende `dim_rationale`** (Fallback-Pfad): „Begründung nicht erfasst" statt Lüge.
- **Reasoning-only / leere Antworten:** als solche markieren.
- **Judge-Resume:** `reports.jsonl` wird ganz neu geschrieben (Reports aus prior+fresh neu berechnet), halb-geschriebene Schlusszeile beim Laden toleriert.

## 8 · Persistenz-Kontrakt & Rückwärtskompatibilität

**Entscheidung (ratifiziert, nicht offen):** `reports.jsonl` wird in **beiden** Judge-Pfaden am Finalize geschrieben — **auch im Default-Pfad** (weder `--web` noch `--emit-events`). Es ist **echte Persistenz**, kein Monitor-Artefakt. Die **Ink.-6-Garantie betrifft nur Event-Files/Callbacks** und bleibt unberührt: der Default-Pfad bekommt **keine neuen Event-Files und keine neuen Callbacks** — aber das Bundle-Verzeichnis enthält künftig zusätzlich `reports.jsonl`. (Das Wort „byte-identisch" gilt also dem Event-/Callback-Verhalten, nicht dem Verzeichnis-Inhalt.) Der Regressionstest, der dies absichert, prüft, dass `reports.jsonl` nach `judge` existiert und sonst keine Event-Files entstehen.

## 9 · Teststrategie (TDD)

Konsistent mit dem Repo (I/O dependency-injected, pure Logik unit-getestet, mypy strict, ruff; GUI-Routen optional/skip ohne `[gui]`).

**Unit (pure):**
- `parse_dimension_report`: verschachteltes Schema → (Scores, Rationales); **blanker int → rationale leer** (Drift-Toleranz); fehlende Begründung → `""`.
- `score_dimensions`: füllt `dim_rationales` (Fake-Judge mit Score+Begründung); **Beleg-Vertrag**: die K.-o.-Dimensions-`rationale` nennt mindestens den K.-o.-relevanten prompt.
- **Geänderte Bestands-Tests** `test_judge.py:119/156` aufs neue Schema heben.
- `reports.jsonl` round-trip (`as_dict` ⇄ Loader), halb-geschriebene Zeile toleriert.
- `bundles.py`: liest Reports aus `reports.jsonl`; **Fallback** auf `scores.csv` ohne `reports.jsonl` (hermetisch, echtes `packs/ndassist.yaml`); parst prompt_id-Zitate; verknüpft K.-o.-Zweig → Wurzel korrekt.
- Sampler: `cpu_pct` im Tick (injizierter `cpu_percent`, geprimet); Loader defaultet Alt-Ticks auf `None`.

**Integration / Verhalten:**
- `TestClient`: `/result/{run}` rendert Urteil + **aktiven K.-o.-Zweig mit Link zur Wurzel** + Scorecard + Dimensions-`rationale` mit klickbaren prompt_ids + Antworten-nach-Kategorie + RAM/CPU-Verlaufsdaten; **Empty-State** für un-judged. `/packs/{pack}` rendert Dimensionen/K.-o./Varianten/Flags. `/` rendert bedeutungstragende Zeilen.
- **Rückwärtskompat:** Alt-Bundle (kein `reports.jsonl`/`cpu_pct`) rendert ohne Crash; Kern ohne `[gui]` funktionsfähig; `judge` schreibt `reports.jsonl` in beiden Pfaden, sonst keine neuen Event-Files (§8).

**Manuell (am Ende):** echter `judge`-Lauf gegen das ndassist-Bundle (Judge :1234) → `reports.jsonl` enthält **belegte** Begründungen → `/result` zeigt „Warum Q6 = 2" mit klickbarem prompt_id-Beleg; ein eval mit CPU-Last → CPU-Spur sichtbar.

## 10 · Scope-Grenze (YAGNI)

**In Phase 1:** die drei Ansichten lückenlos verknüpft (Dimensions-Begründung mit prompt_id-Belegen als Evidenz-Brücke, L8); `dim_rationales` (verschachteltes Schema + Beleg-Vertrag) + `reports.jsonl`; CPU-Sampling (**Rider** — erstes Defer-Kandidat unter Zeitdruck, da nicht Kern-These); Backward-Compat-Fallbacks + Empty-States; RAM/CPU-Verlaufs-Chart (build-frei).

**Bewusst NICHT (→ Phase 2):** Variablen-Entkopplung (Pack-Monolith aufbrechen); im Tool editieren; kontrollierter Vergleich entlang einer Achse (inkl. Hardware-Vergleich gleicher Seed); Steuerung brauchbar machen; CPU im `RAW_CSV_COLUMNS`-Aggregat; **echte per-Prompt→Dimension-Tags im Pack-Schema** (strukturelle Attribution statt Begründungs-Beleg — falls L8 später nicht reicht); Tool-Umbenennung.

## 11 · Berührte / neue Dateien

| Datei | Änderung |
|---|---|
| `ramcheck/judge.py` | verschachteltes Dimensions-Schema im Prompt + **Beleg-Pflicht**; `parse_dimension_report` (+ `parse_dimension_scores`-Wrapper); `score_dimensions` füllt `dim_rationales` |
| `ramcheck/reports.py` *(oder `judge.py`)* | **neu** — `reports.jsonl`-Loader (Writer via `ModelReport.as_dict`) |
| `ramcheck/cli.py` | beide Judge-Pfade schreiben `reports.jsonl` (atomarer Batch-Write am Finalize) |
| `ramcheck/models.py`, `ramcheck/sampler.py` | `ResourceSample.cpu_pct: float\|None=None` (letztes Feld) + `psutil.cpu_percent`-Sampling (geprimet); Loader-Default für Alt-Ticks |
| `ramcheck/gui/bundles.py` | Reports aus `reports.jsonl` (Fallback `scores.csv`); reiches Detail-Objekt; prompt_id-Zitate parsen; K.-o.-Zweig→Wurzel |
| `ramcheck/gui/app.py` | Read-Routen liefern reiche Strukturen + Verlinkungs-Anker |
| `ramcheck/gui/templates/{result,pack,overview}.html` | komplett überarbeitet (Drill-down · Kriterien-Referenz · bedeutungstragende Übersicht) |
| `ramcheck/gui/static/` | leichtes RAM/CPU-Verlaufs-Chart (Inline-SVG/Canvas) |
| `docs/explanation/design-decisions.md` | **neu** — Abschnitt „Bewertungs-Methode": holistische Dimensionen, belegte Begründung als Nachvollziehbarkeits-Brücke, zwei K.-o.-Zweige, gewichtete Master-Scorecard (Quelle für das abrufbare UI-Erklär-Element, L9) |
| `tests/` | neue + **geänderte** Tests je §9 (inkl. `test_judge.py` Schema-Hebung) |
| `AGENTS.md` | `reports.jsonl` (ModelReport-Persistenz, beide Judge-Pfade) + `cpu_pct` im Sampler dokumentieren |

## 12 · Offene Detailpunkte (für den Implementierungsplan)

- **Verlaufs-Chart-Form:** Inline-SVG-Sparkline selbst gezeichnet vs. winzige vendored Lib (uPlot ~40 kB). Default: selbstgezeichnetes SVG.
- **prompt_id-Zitat-Extraktion:** wie robust prompt_ids aus dem `rationale`-Freitext geparst werden (Regex auf bekannte Pack-prompt-ids vs. den Judge zu einem separaten Feld zwingen). Default: gegen die bekannten prompt-ids des Packs matchen (kein neues Judge-Feld nötig); im Plan entscheiden.
