# B1 — Verschmolzene Single-Run-Ergebnisseite

**Datum:** 2026-06-24 · **Projekt:** B „GUI-Restrukturierung", Sub-Projekt 1 von 3
**Status:** Design freigegeben, bereit für Implementierungsplan

## Kontext & Motivation

Die GUI zeigt das Ergebnis *eines* Bundles heute über **zwei** Seiten:

- `/result/{name}` (`result.html`, 479 Z.) — Single-Run-Detail: Master-Scorecard (alle
  Modelle×Varianten, holistische %), K.-o.-Block, alle Antworten nach Kategorie + Judge-
  Verdicts, Perf-/Ressourcen-Verlauf, Export.
- `/compare/{name}` (`compare_axis.html`, 132 Z.) — Achsen-Vergleich *im selben Bundle*:
  Effizienz-Scatter, Kopf-an-Kopf-Tabelle (Modelle *oder* Varianten als Spalten, mit 🏆),
  Per-Aufgabe-Δ.

Beide ziehen aus demselben Bundle (`bundle_detail` bzw. `compare.compare_detail` +
`compare.axis_options`) und überlappen stark (Qualität-%, Dimensionen, Rubrik/Sicherheit,
Perf) — einmal als Zeilen-Tabelle, einmal als Spalten-Vergleich. Das Hin-und-Her-Springen
kostet Kontext; die Doppelung der Datenladung kostet Code-Kohärenz.

**Ziel:** Eine einzige Single-Run-Ergebnisseite, die Vergleich *und* Detail-Tiefe trägt.
`compare_axis.html` entfällt.

## Nicht-Ziele (Scope-Grenzen)

- **`/compare` (ohne Name)** — das Cross-Run-Aggregat Hardware×Qualität (Station 6,
  `compare.html`) — bleibt **unberührt**. Das ist Sub-Projekt **B2** (dedizierte
  Cross-Run-Vergleichsseite). Nur der *Innerhalb-Bundle*-Achsenvergleich `/compare/{name}`
  wird hier verschmolzen.
- Konfig+Start / Pack-Editor ist **B3** — hier nicht berührt.
- Keine neuen Metriken/Bewertungslogik; rein Darstellungs-/Routing-/Code-Konsolidierung
  über der bestehenden, kanonischen `result.json`-Datenbasis (Projekt A).

## Design-Entscheidungen (im Brainstorming ratifiziert)

1. **Optimiert für „beides gleichwertig":** model- und variant-Achse werden gleichwertig
   bedient; der Achsen-Umschalter ist first-class. Universell statt auf einen Bundle-
   Zuschnitt zugeschnitten.
2. **Informationsarchitektur: eine Scroll-Seite, Vergleich zuerst.** Keine Tabs (würden
   Inhalt verstecken, JS-State brauchen, Strg-F/Druck/Export-als-Ganzes brechen). Ein
   langer, durchsuchbarer, druckbarer DOM.
3. **Adaptive Tabellen:** Bei 1×N oder N×1 ist die Kopf-an-Kopf-Tabelle bereits die
   vollständige Übersicht — keine separate Scorecard. Nur bei echter Matrix (N Modelle ×
   M Varianten) kommt zusätzlich eine kompakte flache Master-Scorecard dazu. Wahrt die
   Achsen-Semantik (Projektion: bei „Achse: Modell" wird *eine* Variante fixiert, damit
   nicht Modell-A-baseline gegen Modell-B-none verglichen wird) und eliminiert die
   Redundanz im häufigen Fall.
4. **Achsen-Umschalter server-seitig** (Query-Param + Reload, wie heute): kein neuer
   JS-State, jede Achsen-Ansicht verlinkbar/druckbar.
5. **Antworten-Sektion bei mehreren Zellen: Zell-Filter** („alle zeigen" als Option).
   **Default-Zelle** = die mit höchster holistischer Qualität-% (Gleichstand → höchstes
   `rubric_level`, dann erste in Bundle-Reihenfolge) — spiegelt die Winner-Logik. Der
   *vergleichende* per-Prompt-Blick kommt aus der Per-Aufgabe-Δ-Sektion; die Antworten-
   Sektion ist die Tiefe.

## Architektur

### Routing & Seiten-Identität

- `/result/{name}` wird **die eine** Single-Run-Seite; nimmt die Achsen-Query-Params
  (`axis=model|variant`, `variant=…`/`model=…` als Projektion) entgegen.
- `/compare/{name}` → **301-Redirect** auf `/result/{name}?axis=…&…` (Bookmarks/Backlinks/
  generierte Links nicht brechen). Die Route bleibt als dünner Redirect bestehen.
- `compare_axis.html` wird gelöscht.

### Seitenstruktur (eine Scroll-Seite, von oben)

**Vergleichs-Block** (vom Achsen-Umschalter gesteuert):

1. Kopf-Zeile: Bundle · Pack (→ Kriterien) · Judge (Modell/Temp) · Achsen-Umschalter
   (Modell ↔ Variante) + Projektions-Wahl.
2. Effizienz-Scatter (Qualität × Decode, Punktgröße = Peak-RAM `sys_used_mb`) — nur ≥2 Zellen.
3. Adaptive Tabelle(n): Kopf-an-Kopf (Achsen-Schnitt, 🏆 + Dimensionen + Perf); bei echter
   Matrix zusätzlich kompakte flache Master-Scorecard.
4. Per-Aufgabe-Δ (aufklappbar) — nur ≥2 Zellen.
5. K.-o.-Block (⛔) — prominent bei ausgelöster Regel.

**Detail-Block:**

6. Antworten nach Kategorie (volle Liste + Judge-Verdicts/Rationales) mit Zell-Filter.
7. Perf + Ressourcen-Verlauf (RAM/CPU-Sparkline je Zelle).
8. Export.

### Single-Cell-Degradation

Bei genau einer Zelle (1×1): Scatter, Kopf-an-Kopf-Spaltenvergleich und Per-Aufgabe-Δ
entfallen (nichts zu vergleichen); die Seite zeigt Scorecard-Werte der einen Zelle +
Detail-Block. Achsen-Umschalter nur sichtbar, wenn die jeweils andere Achse ≥2 Werte hat.

### Code-Konsolidierung

- **Eine Detail-Struktur** speist die Route. `bundles.bundle_detail()` und
  `compare.compare_detail()`/`axis_options()` laden heute beide Pack/Responses/Verdicts/
  Reports. Zusammenführen zu *einem* Ladepfad; `compare.py` bleibt die **pure** Achsen-/
  Scatter-/Winner-/Projektions-Logik (unit-getestet), wird aber über die gemeinsame
  Datenstruktur angesteuert statt eigenständig zu laden.
- `result.html` schluckt den `compare_axis`-Inhalt → wird groß. **Gegenmaßnahme:**
  Sektionen in Template-Partials/Macros auslagern (`templates/macros/`, Muster wie
  `_method_explainer.html`), damit keine ~700-Zeilen-Datei entsteht. Eine Datei = eine
  klar umrissene Sektion.

## Datenfluss

`result(name, axis, variant|model)` → gemeinsamer Loader (Pack + responses.jsonl +
judgements.jsonl + reports.jsonl + resources.jsonl, einmal) → `scorecard.master_rows` +
`compare`-pure-Logik (axis_options, scatter_points, winners, divergence, adaptive
Tabellen-Auswahl) → ein Detail-Dict → `result.html` (+ Partials). Kanonische Quelle bleibt
`result.json` / die Bundle-jsonl-Artefakte; nichts wird neu berechnet, was Projekt A schon
kanonisch ablegt.

## Tests

- Route-Tests (`test_gui_app_detail.py`, `test_gui_compare_route.py`) zusammenführen/
  erweitern: adaptive Tabellen-Auswahl (1×N vs N×M vs 1×1), Redirect `/compare/{name}` →
  `/result/…?axis=…`, Achsen-Umschalter-Param (model/variant + Projektion), Single-Cell-
  Degradation, fehlendes/unbewertetes Bundle (kein 500).
- Pure Logik in `compare.py` bleibt unit-getestet (axis_options, scatter, winners,
  divergence, adaptive-Auswahl).
- Bestehende Honesty-/Provenance-Invarianten aus Projekt A bleiben grün.

## Risiken & Gegenmaßnahmen

- **`result.html` wird zu groß** → Partials/Macros (s. o.).
- **Redirect bricht Tiefenlinks** → Query-Params vollständig durchreichen; Test deckt es ab.
- **Doppelte Datenladung schleicht zurück** → ein Loader, `compare.py` lädt nicht selbst.
- **Verwechslung `/compare` (B2) ↔ `/compare/{name}` (hier)** → in Spec + Tests explizit
  getrennt; `/compare` bleibt unangetastet.
