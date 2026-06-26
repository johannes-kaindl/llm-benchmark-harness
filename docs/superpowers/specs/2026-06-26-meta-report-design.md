# Spec: Meta-Report — ±Judging-Export über N gewählte Läufe (Sub-Projekt E)

**Datum:** 2026-06-26 · **Branch:** `feat/gui-meta-report` · **Status:** approved

## Problem / Ziel

`/compare` (Station 6) kann **zellgenau auswählen** (`PoolRow` je `run_name|model|variant`, Checkbox →
`?rows=<id>&rows=<id>…`) und zeigt für ≥2 Zellen einen Cross-Run-**Diff** (`diff_rows` → Leaderboard +
🏆-Trophäen). Aber: **dieser Vergleich lässt sich nicht exportieren.** Es gibt keinen Weg, aus einer
gewählten Mehrfach-Auswahl *ein* publizierbares bzw. an eine Cloud-KI weiterreichbares Dokument zu
erzeugen.

E fügt einen **Meta-Report-Export** auf `/compare` hinzu: aus N gewählten Zellen entsteht **ein
Markdown** (Hybrid: Summary oben + zellgenaues Detail unten), **mit oder ohne Judging**, plus eine
**Leaderboard-CSV** als separater Download. Der `±Judging`-Schalter spiegelt das bestehende
`/export-report?judging=0|1`-Muster und ist das Fundament für **Sub-Projekt F** (Cloud-KI bewertet die
Judge-Qualität anhand des `judging=0`-Bewertungs-Auftrags).

## Nicht-Ziele (v1, YAGNI)

- **Kein** neuer Picker — der Einstieg ist die *bestehende* `/compare`-Auswahl (Knopf, kein UI-Neubau).
- **Kein** PDF/DOCX/HTML-Artefakt (AGENTS.md: persistiert wird nur Markdown + CSV).
- **Kein** Zip-Bundle — MD und CSV sind **zwei separate Downloads** (so vom User entschieden).
- **Keine** gemischte judged/eval-only-Auswahl zu behandeln — vom `/compare`-Pool aus **unmöglich**
  (siehe „Verifizierte Annahme").
- **Keine** Änderung an der Mess-/Bundle-/Judge-Logik. Reiner Lese-/Render-Pfad.
- **Keine** Multi-Pack-Optimierung — Multi-Pack wird sauber gerendert, aber der gedachte Fall ist
  *eine* Use-Case-Achse (Modelle/Varianten desselben Packs).

## Verifizierte Annahme (load-bearing)

`aggregate.pool_rows` (`aggregate.py:119-161`) baut **ausschließlich aus `scores.csv`**
(`base.rglob("scores.csv")`). Ein nur `eval`'tes, nie `judge`'tes Bundle hat **keine** `scores.csv` →
es erscheint im `/compare`-Pool **gar nicht**. **Folge:** jede vom `/compare`-Einstieg wählbare Zelle ist
bereits gejudgt. Die `±Judging`-Frage des Handoffs („gemischte judged/eval-only-Auswahl?") kollabiert
damit auf eine saubere **globale** Semantik: Urteile **zeigen** vs. **verbergen → frischer
Bewertungs-Auftrag**. (Eine gejudgte Zelle *kann* `quality_pct=None` haben — z.B. nur reasoning-only;
das ist heute schon graceful und bleibt es.)

## Architektur-Entscheidung: Sektions-Zerlegung (Ansatz 1)

`render_report_md` (`report_md.py:283-640`, ~360-Zeilen-Monolith) erzeugt einen **kompletten
Standalone-Report**: eigenes YAML-Frontmatter, TOC, Bewertungs-Methode, Glossar. **N davon zu
konkatenieren bricht** (mehrere `---`-Frontmatter-Blöcke = ungültiges Obsidian; N-fach Methode/Glossar).
Der Hybrid-Report muss die Sektionen **komponieren**, nicht aneinanderkleben.

Gewählt (vor 2 Alternativen — Standalone-Reimplementierung [dupliziert Logik, Drift-Risiko] und
String-Chirurgie am gerenderten MD [brüchig]):

> **`render_report_md` verhaltenserhaltend in pure Sektions-Helfer zerlegen, `meta_report` komponiert
> dieselben Helfer.** Recycelt die *getestete* Render-Logik (Verdict-Badges, reasoning-only,
> Längen-Bias-Warnung, zitierte prompt_ids, ausfüllbare Scorecard) 1:1; eine Single-Source-of-Truth pro
> Sektion; gesünderes `report_md`. `render_report_md` wird ein dünnes Compose → **bytegleiche** Ausgabe,
> abgesichert durch die bestehenden Report-Tests.

## Design

### Sektions-Zerlegung — `touchstone/gui/report_md.py`

Die per-Bundle-Sektionen werden als pure modul-ebene Helfer extrahiert (Namen final im Plan), je
`-> str`:

```
frontmatter(detail, *, include_judging)         # YAML — nur der Meta-Report ersetzt dies
section_overview_urteil(detail, include_judging) # Überblick & Urteil (Master-%/Safety)
section_eval_task(detail)                        # „Bewertungs-Auftrag" (ausfüllbare Scorecard) — existiert (Z. 201)
section_methode()                                # Bewertungs-Methode (statisch)
section_hardware(detail)                          # Hardware & Konfiguration
section_master_scorecard(detail)                  # gejudgte Master-Scorecard (nur wenn reports da)
section_dimensionen(pack)                          # Dimensionen-Definitionen (pack-spezifisch)
section_prompt_varianten(pack)                     # System-Prompt-Texte (pack-spezifisch)
section_prompts_antworten(detail, glossary, include_judging)  # Prompts & Antworten (+Verdict-Badges)
section_glossar(glossary)                          # Metrik-Glossar
```

`render_report_md(detail, glossary, *, include_judging=True)` bleibt **öffentlich + signaturgleich**
und ruft die Helfer in heutiger Reihenfolge — Ausgabe **bytegleich** (Regressionstest).

### Pure Logik — `touchstone/gui/meta_report.py` (neu)

```python
def filter_detail_to_cells(detail: dict, cells: set[tuple[str, str]]) -> dict:
    """Engt responses/master_rows/reports/verdicts/cited_ids auf die gewählten (model,variant)-
    Paare ein; pack/manifest/hardware/perf/cpu/ram bleiben unangetastet. Reiner Filter, kein I/O."""

def render_meta_report_md(
    selected: list[PoolRow], details: list[dict],
    glossary: Mapping[str, Glossary], *, include_judging: bool,
) -> str:
    """Hybrid-Report: ein Meta-Frontmatter + Summary + zellgenaues Detail je Bundle +
    EINMAL geteilt Methode + Glossar."""

def render_meta_leaderboard_csv(selected: list[PoolRow]) -> str:
    """Eine Zeile je gewählte Zelle (Quality + Effizienz) — immer mit echten Daten (kein judging-Toggle)."""
```

`details` ist 1 Eintrag je **distinktem `run_name`** (per `bundle_detail` geladen, dann
`filter_detail_to_cells` auf die in *diesem* Bundle gewählten Zellen). Reihenfolge folgt der
Auswahlreihenfolge der Zellen.

### Report-Aufbau (`render_meta_report_md`)

```
---  (EIN Meta-Frontmatter: title, type, date, N Zellen, Packs, Modelle, judging-Modus)  ---
# Meta-Report · <pack(s)> · N Zellen
## Summary
   - Gemeinsame Dimensionen (diff.common) · variierende (diff.varying)
   - 🏆 Leaderboard (Zellen = Zeilen, sort. n. quality_pct desc; bei judging=0 sort. n. decode desc):
     | Modell·Variante | [Quality %] | Decode | TTFT P50 | Modell-Δ | System-Peak |
     · judging=1: Quality-Spalte + 🏆-Markierung je Metrik-Sieger (diff.winners)
     · judging=0: Quality-Spalte ENTFÄLLT, keine Quality-Trophäe → unbiased für F
   - Die Metrik-Zeilen kommen direkt aus `selected` (PoolRows); common/varying/winners reichert
     `diff_rows(selected)` **nur bei ≥2 Zellen** an. Bei genau 1 Zelle: common = alle Dimensionen,
     keine winners, keine Trophäen (kein `diff_rows`-Aufruf mit n=1).
## Detail   (je distinktem Bundle, in Auswahlreihenfolge)
   ### Bundle <run_name> (<pack>, Hardware-Kurzz.)
       section_dimensionen / section_prompt_varianten  (1× je distinktem Pack, oben gesammelt)
       section_prompts_antworten(gefiltert)   # NUR die gewählten Zellen dieses Bundles
       judging=1 → section_master_scorecard(gefiltert) · judging=0 → section_eval_task(gefiltert)
## Bewertungs-Methode    (section_methode — EINMAL)
## Metrik-Glossar        (section_glossar — EINMAL)
```

- **judging=0 = „Bewertungs-Auftrag":** Quality nirgends sichtbar (Summary-Spalte weg, Detail =
  ausfüllbare Scorecard, keine Verdict-Badges) → eine re-judgende Cloud-KI wird nicht am alten Urteil
  verankert. Dateiname-Suffix `-zum-bewerten`.
- **1 Zelle** ist erlaubt (Einzel-Bewertungs-Auftrag): Leaderboard = eine Zeile, **keine** Trophäen.
- **Multi-Pack:** `section_dimensionen`/`section_prompt_varianten` je distinktem Pack einmal; Summary
  weist Pack als variierende Dimension aus (kommt aus `diff_rows`).

### Routen — `touchstone/gui/app.py`

1. **`GET /export-meta-report?rows=<id>&rows=<id>…&judging=0|1`** (Default `judging=1`):
   `pool_rows(runs_dir)` → auf `rows` filtern + in Auswahlreihenfolge sortieren (1:1 die `/compare`-Logik,
   Z. 324-327) → nach `run_name` gruppieren → je distinktem Run: `_confine(run_name)`,
   `bundle_detail(rd)` (None → Zelle überspringen + Notiz), `filter_detail_to_cells` →
   `render_meta_report_md(selected, details, GLOSSARY, include_judging=bool(judging))`. Antwort:
   `Response(md, media_type="text/markdown; charset=utf-8", Content-Disposition attachment)`,
   Dateiname `touchstone-meta-report-<N>-zellen.md` bzw. `…-zellen-zum-bewerten.md` (judging=0).
   **Leere/komplett-unbekannte Auswahl → 400.**
2. **`GET /export-meta-csv?rows=<id>…`**: selektieren wie oben →
   `render_meta_leaderboard_csv(selected)` → `Response(csv, media_type="text/csv; charset=utf-8",
   attachment)`, Dateiname `touchstone-meta-leaderboard-<N>-zellen.csv`. **Kein** judging-Param.
   Leere Auswahl → 400.
   CSV-Spalten: `run_name, model, variant, pack, pack_version, chip, ram_gb, quant, quality_pct,
   ttft_p50, decode_med, model_delta_gb, peak_ram_gb, power`.

### UI — `templates/compare.html` (Sticky-Footer)

Im bestehenden Footer (`x-show="sel.length > 0"`), neben „Vergleichen":
- Toggle **„mit Bewertung"** (`x-data`-Bool, Default **an**) — steuert nur den `.md`-Knopf.
- `[ Meta-Report .md ]` → `window.location = '/export-meta-report?' +
  sel.map(id=>'rows='+encodeURIComponent(id)).join('&') + '&judging=' + (mitBewertung?1:0)`.
- `[ Leaderboard .csv ]` → `'/export-meta-csv?' + sel.map(...).join('&')`.

Recycelt exakt das vorhandene URL-Bau-Muster (Z. 105) — kein neues JS-File, inline-Alpine.

## Sicherheit (entschieden)

- Jeder `run_name` einzeln über `_confine` (resolve + `is_relative_to(runs_dir)`) → 404 bei
  Traversal/Symlink-Escape; Client-`rows`-Array nie blind als Pfad nutzen.
- Nur Lese-Pfad — keine state-ändernde Mutation; GET ist korrekt (idempotent, teilbar via URL).
- `GLOSSARY` + `render_report_md`-Autoescape-Verhalten unverändert (Jinja-Sektionen bleiben in den
  Helfern; meta_report fügt nur MD-Text zusammen, keine neue HTML-Render-Fläche).
- Transiente Dateien (`run.json`, `events.jsonl`, …) sind über `bundle_detail`/Discovery ohnehin
  ausgeschlossen — der Meta-Report liest nur den geladenen `detail`.
- Pro-Zelle-Ergebnis statt alles-oder-nichts: eine nicht ladbare Zelle bricht den Report nicht ab.

## Tests (TDD, RED→GREEN)

**Regression — `report_md`-Zerlegung** (`tests/test_gui_report_md.py`, bestehend): `render_report_md`
bleibt **bytegleich** für judging=1 *und* judging=0 (Golden-Vergleich vor/nach Refactor).

**Pure `meta_report.py`** (`tests/test_gui_meta_report.py`, neu):
- `filter_detail_to_cells`: behält nur gewählte (model,variant) in responses/master_rows/reports/
  verdicts/cited_ids; lässt pack/hardware/perf intakt; leere Zellmenge → leere Listen.
- `render_meta_report_md` judging=1: Leaderboard-Zeile je Zelle, 🏆 beim Sieger, Quality-Spalte da,
  Detail nur gewählte Zellen, Methode + Glossar **genau 1×**.
- judging=0: **keine** Quality-Spalte/Trophäe im Summary, Detail = ausfüllbarer Bewertungs-Auftrag,
  keine Verdict-Badges.
- 1 Zelle → Leaderboard ohne Trophäen, kein Crash.
- Multi-Pack-Auswahl → Dimensionen je Pack einmal.
- `render_meta_leaderboard_csv`: Header + eine Zeile je Zelle, korrekte Spalten, Quality auch bei
  judging-Kontext immer befüllt (None → leer).

**Routen** (`tests/test_gui_meta_report_routes.py`, TestClient, runs_dir injiziert):
- `/export-meta-report` 200, `text/markdown`, Dateiname je judging-Variante; Inhalt enthält Summary +
  gewählte Zellen.
- `judging=0` → `-zum-bewerten.md`, keine Quality im Summary.
- `/export-meta-csv` 200, `text/csv`, Header + N Zeilen.
- Leere/unbekannte `rows` → 400 (beide Routen).
- Traversal in `rows` (`../…`) → 404, kein Escape.
- Eine Zelle aus einem korrupten Bundle → übersprungen + Notiz, kein 500.

**UI** (`tests/test_gui_*compare*`): Footer trägt Toggle + beide Export-Knöpfe; URLs bauen `rows=`
+ `judging=` korrekt.

**Headless-Smoke** (Memory `gui-restart-after-changes`): frischer Server; Temp-`runs/` mit ≥2 gejudgten
Dummy-Bundles → beide Routen ziehen → MD enthält Leaderboard + Detail, CSV hat N Zeilen; judging=0 ohne
Quality.

**Adversariale Whole-Branch-Review** vor Merge (3 Linsen; Controller verifiziert jeden Fund selbst) —
Fokus: Confinement der neuen Routen, Quality-Leakage bei judging=0 (darf NIRGENDS sichtbar sein),
Bytegleichheit der `report_md`-Zerlegung.

## Implementierungs-Reihenfolge (Plan)

1. **`report_md`-Zerlegung** in Sektions-Helfer, `render_report_md` als Compose; Golden-Regressionstest
   (bytegleich, judging an/aus) zuerst RED (Golden festschreiben) → GREEN nach Refactor.
2. Pure `meta_report.py` (`filter_detail_to_cells`, `render_meta_report_md`, `render_meta_leaderboard_csv`)
   + Unit-Tests (RED→GREEN).
3. Routen `/export-meta-report` + `/export-meta-csv` + Route-Tests.
4. `compare.html` Footer-Toggle + zwei Knöpfe + UI-Test.
5. Volle Suite + `mypy touchstone/` + `ruff check . && ruff format --check .` + Headless-Smoke.
6. Adversariale Review → bestätigte Funde fixen → Merge nach `main` + Push (Codeberg→GitHub-Mirror).

## Berührte Dateien

- `touchstone/gui/meta_report.py` (neu) · `touchstone/gui/report_md.py` (Sektions-Zerlegung,
  signatur-/byte-erhaltend) · `touchstone/gui/app.py` (2 GET-Routen) · `templates/compare.html`
  (Footer-Toggle + 2 Knöpfe).
- `tests/test_gui_meta_report.py` (neu) · `tests/test_gui_meta_report_routes.py` (neu) ·
  `tests/test_gui_report_md.py` (Golden-Regression) · bestehende compare-UI-Tests ergänzt.

## Risiken / Edge-Cases

- **Bytegleichheit der Zerlegung:** größtes Risiko. Golden-Test *vor* dem Refactor festschreiben
  (Snapshot der heutigen `render_report_md`-Ausgabe für je ein judged + ein eval-only Fixture-Bundle),
  dann refactoren bis grün.
- **Quality-Leakage bei judging=0:** Review-Schwerpunkt — Quality darf weder im Summary noch in Detail-
  Badges/Scorecards noch im Frontmatter auftauchen. (CSV ist davon ausgenommen — eigener Download, kein
  Cloud-KI-Futter.)
- **Stale Auswahl** (ein Bundle wird zwischen `/compare`-Laden und Export getrasht/gelöscht):
  `bundle_detail`→None → Zelle still überspringen + Report-Notiz, kein Hard-Fail.
- **Sehr breite Auswahl** (viele Zellen): Report wird lang, aber lokal/Einzelnutzer — akzeptabel v1.
- **Multi-Pack-Auswahl:** funktioniert (Dimensionen je Pack), ist aber nicht der Optimierungsfall; die
  Summary-`common/varying`-Logik aus `diff_rows` trägt das bereits.
