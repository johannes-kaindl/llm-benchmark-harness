# B2 — Cross-Run-Vergleichsseite

**Datum:** 2026-06-25 · **Projekt:** B „GUI-Restrukturierung", Sub-Projekt 2 von 3
**Status:** Design freigegeben, bereit für Implementierungsplan

## Kontext & Motivation

`/compare` (`compare.html`, 53 Z.) zeigt heute **automatisch alle** bewerteten Läufe als
flache Aggregat-Tabelle: `aggregate.aggregate(load_all_scores(runs_dir))` gruppiert jede
`scores.csv`-Zeile nach `(chip, ram_gb, pack, pack_version, model, quant, variant)`, mittelt
Wiederholungen, sortiert nach Chip+Qualität. Keine Auswahl, kein Fokus — bei vielen Läufen
unübersichtlich, und Wiederholungen desselben Setups verschwinden im Mittel.

Der Projektkern ist Cross-System-Vergleichbarkeit: *derselbe Code auf M1 (LM Studio) und M5
(mlx)* — „nur die Config ändert sich" — und **publizierbare, von vielen beigesteuerte Läufe**.
Genau dafür fehlt eine **kuratierte** Vergleichssicht: konkrete Läufe auswählen (auch
importierte fremde) und strukturiert gegenüberstellen.

**Ziel:** `/compare` wird eine Auswahl-+-Vergleichsseite — Läufe wählen/importieren, dann ein
**Auto-Diff**-Vergleich der gewählten Zeilen.

## Design-Entscheidungen (im Brainstorming ratifiziert)

1. **Universelle Seite, eine Mechanik.** Cross-Machine, Cross-Model/Quant und kuratierter
   Community-Vergleich sind **Lesarten** derselben „N Zeilen auswählen → gegenüberstellen"-
   Seite, nicht getrennte Features. Was sie unterscheidet: was variiert und was betont wird.
2. **Vergleichseinheit = Einzelzeile** `(HW × Modell × Quant × Variante)` — die feinste
   vergleichbare Einheit (genau eine Zahl pro Metrik).
3. **Ersetzen/integrieren — eine Seite.** B2 wird `/compare`: Auswahl-Pool oben, Vergleich
   unten. Keine zweite Route, keine Duplikation (dieselbe Konsolidierungs-Linie wie B1).
4. **Auto-Diff-Hervorhebung.** Die Seite erkennt automatisch, welche Dimensionen über die
   Auswahl **variieren** (→ Spalten-Köpfe) und welche **konstant** sind (→ einmal als
   „GEMEINSAM" oben). Kein manueller Achsen-Modus.
5. **Auswahl-Einheit = einzelne Bundle-Zeile** (`run_dir × model × variant`), **nicht** die
   gemittelte Gruppe — so sind zwei Läufe desselben Setups gegenüberstellbar (Varianz/
   Regression), und Provenance (Datum, „importiert") bleibt sichtbar. Stabile ID:
   `run_dir|model|variant`.
6. **Server-seitiger `?rows=`-Vergleich.** Checkboxen sammeln clientseitig (Alpine); ein
   „Vergleichen"-Button schickt die IDs als Query-Param → server rendert (verlinkbar/druckbar,
   wie B1's Achsen-Umschalter).
7. **Import = simples Upload-Feld** über den bestehenden `/import-bundle`-Endpoint (Drag-Drop
   = spätere Politur, YAGNI).

## Nicht-Ziele (Scope-Grenzen)

- **Später, eigene Specs:** Effizienz-Scatter cross-run (Wiederverwendung `scatter.js`),
  Provenance-Badges, Drill-down zu einzelnen Antworten, Permalink-/Teilen-Funktion.
- `/compare/{name}` (der B1-Redirect) bleibt **unberührt**.
- B3 (Konfig+Start / Pack-Editor) ist nicht Teil von B2.
- Keine neuen Metriken; rein Auswahl-/Darstellungs-/Diff-Logik über den bestehenden
  `scores.csv`-Daten (und damit `result.json`/Projekt A als Quelle).

## Architektur

### Seiten-Struktur (eine Scroll-Seite)

**Zone 1 — Auswahl-Pool (oben):**
- Die Tabelle aller verfügbaren Bundle-Zeilen (chip, ram, pack, model, quant, variant +
  Datum/Provenance) mit **Auswahl-Checkbox** je Zeile und stabiler ID `run_dir|model|variant`.
- **Filter** (client-seitig, Alpine): Chip / Pack / Modell.
- **Import-Feld:** Datei-Upload → `/import-bundle`; nach Erfolg Reload, der Lauf erscheint im
  Pool.
- **„Vergleichen"-Button:** sammelt die angehakten IDs → navigiert zu `/compare?rows=…`.

**Zone 2 — Auto-Diff-Vergleich (unten, ab ≥2 gewählten):**
- **GEMEINSAM-Block:** Dimensionen, die über alle gewählten Zeilen konstant sind, einmal oben.
- **Spalten** = die gewählten Zeilen, Kopf zeigt die **variierenden** Dimensionen.
- **Metrik-Zeilen:** Qualität %, TTFT P50, Decode-Median, Peak-RAM, Modell-Delta — mit
  **Winner-Marker** 🏆 pro Metrik (Gleichstand → keiner, wie B1's `_winners`).

### Datenfluss & Code

- `aggregate.py` ist heute pure Logik. **Die bestehende `aggregate()` (über Bundles
  gemittelt) bleibt unverändert** — sie speist den CLI-Pfad `touchstone aggregate`
  (`scores_all.csv` + md). B2 **fügt** eine separate, pro-Bundle-Funktion **hinzu**:
  - Eine Liefer-Funktion, die **pro Bundle** (nicht über Bundles gemittelt) eine Zeile mit
    `run_dir`-Herkunft + stabiler ID `run_dir|model|variant` liefert. `load_all_scores` muss
    die `run_dir` aus dem Dateipfad anreichern (additiv, ohne die gemittelte `aggregate()`-
    Nutzung zu brechen).
  - Eine pure Funktion `diff_rows(selected_rows) -> (common: dict, varying: list[str],
    columns: list[Row])`: ermittelt konstante vs. variierende Dimensionen + die Spalten.
  - Eine `winners(columns) -> dict[metric, id|None]`-Funktion (oder Wiederverwendung des
    B1-Musters) für die 🏆-Marker; Gleichstand → None.
- Route `GET /compare` nimmt optional `?rows=<id>,<id>,…`: ohne → nur Pool; mit ≥2 gültigen →
  Pool + Vergleich. Unbekannte/kaputte IDs werden ignoriert (nie 500).
- `compare.html` neu; Sektionen in Partials (`templates/macros/`, Muster wie B1's
  `_compare_block.html`), damit keine überladene Datei entsteht.

## Tests

- **Pure Logik (unit):** `diff_rows` — common/varying-Erkennung (alle gleich, eine Dim
  variiert, mehrere variieren), Einzel-Bundle-Zeilen-IDs (zwei Läufe gleichen Setups bleiben
  unterscheidbar), Winner inkl. Gleichstand→None, Edge-Cases 0/1/N Zeilen.
- **Route (TestClient):** `/compare` ohne `rows` (nur Pool), mit gültigen `rows` (Vergleich
  rendert GEMEINSAM + variierende Köpfe), leere/kaputte `rows` (kein 500), Import-Roundtrip
  (Upload → Lauf erscheint im Pool).
- Bestehende `aggregate`-Tests bleiben grün (oder werden um die `run_dir`-Herkunft erweitert,
  ohne die Cross-Run-Aggregat-Semantik zu brechen).

## Risiken & Gegenmaßnahmen

- **Zeilen-ID-Stabilität:** `run_dir|model|variant` muss URL-sicher sein (run_dir-Namen sind
  Zeitstempel-Slugs — safe; defensiv encoden).
- **`scores.csv` trägt keine `run_dir`:** die Herkunft kommt aus dem Dateipfad beim Laden —
  `load_all_scores` muss sie anreichern, ohne die bestehende Aggregat-Nutzung zu brechen.
- **Pool wird groß** bei vielen Läufen → client-seitige Filter; Pagination ist Future, nicht
  MVP.
- **`compare.html` wächst** → Partials/Macros (wie B1).
- **Verwechslung `/compare` (diese Seite) ↔ `/compare/{name}` (B1-Redirect):** in Spec +
  Tests getrennt; der Redirect bleibt unangetastet.
