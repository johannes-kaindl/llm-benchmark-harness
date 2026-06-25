# B3a — Konfig+Start als Flow-Stationen

**Datum:** 2026-06-25 · **Projekt:** B „GUI-Restrukturierung", Sub-Projekt 3a von 3
**Status:** Design freigegeben, bereit für Implementierungsplan

## Kontext & Motivation

`/config` (`config.html`, 165 Z., Sidebar-Station „3·Konfig+Start") zeigt heute **drei
gleichwertige, parallele Cards**: **Eval starten** (Pack + Config + Modell-Picker),
**Judge starten** (eval-only-Bundle + Judge-Config + Judge-Modell), **Bundle importieren**.
Die Karten stehen nebeneinander, spiegeln aber nicht den tatsächlichen Ablauf
(Eval → erzeugt ein Bundle → Judge bewertet es → Ergebnis ansehen). Man sieht nicht, was der
nächste Schritt ist.

**Ziel:** Einen **klaren Flow / eine sichtbare Reihenfolge** herstellen — die Seite als
geführte, aber nicht erzwungene Stations-Sequenz statt paralleler Karten.

## Projekt-Zuschnitt

B3 „Konfig+Start neu + Pack-Editor" wird in **zwei unabhängige Sub-Projekte** zerlegt:
- **B3a (dieses):** Konfig+Start-Flow-Redesign.
- **B3b (separat danach):** Pack-Editor (`pack.html`-Viewer → Editor) — eigene Spec, eigene
  Brainstorming-Runde (Editor-Tiefe/Validierung/Export).

## Design-Entscheidungen (im Brainstorming ratifiziert)

1. **Primäres Ziel = klarer Flow / Reihenfolge** (nicht kognitive-Last oder Vorschau —
   die sind sekundär, nicht Scope von B3a).
2. **Flow-Stationen, je eigenständig.** Die Seite zeigt den Lebenszyklus als sichtbare
   Sequenz — **Eval starten → Judge starten → Ergebnis ansehen** — aber jede Station ist
   eigenständig startbar (kein erzwungener Wizard). Begründung: Eval und Judge laufen
   zeitlich getrennt (man judged oft ein Bundle aus einer früheren Sitzung); ein starrer
   Schritt-Zwang würde das brechen.
3. **Nav-Konsistenz:** der In-Seite-Flow nutzt **keine 1/2/3-Zahlen** (die Sidebar nummeriert
   Seiten-Stationen 1/3/6) — Marker sind die Aktions-Namen + Pfeile.
4. **Kontextabhängige Default-Station.** Beim Laden: existiert **kein** wartendes
   eval-only-Bundle → **Eval** aufgeklappt (häufigster Einstieg); existieren eval-only-Bundles
   → ein **dezenter Hinweis** an der Judge-Station („N Bundle(s) bereit zum Bewerten"). Macht
   den nächsten sinnvollen Schritt deterministisch sichtbar (ND-freundlich).
5. **Import + Resume als Nebeneinstiege** — keine Haupt-Stationen, kompakt + klar abgesetzt.
   Resume erscheint nur im bestehenden `?resume=…`-Modus (Einstieg von der Übersicht), **keine**
   neue crashed-Bundle-Liste.
6. **Kein Funktions-Umbau.** Die bestehende Funktionalität bleibt 1:1 — Modell-Picker
   (Checkboxen + Ad-hoc + „vom Endpoint hinzufügen"), Config-ⓘ-Vorschau, Judge-Modell-Picker,
   Conflict/Error-Banner. B3a **ordnet** in den Flow um, baut nicht funktional neu.

## Nicht-Ziele (Scope-Grenzen)

- **B3b (Pack-Editor)** — nicht Teil von B3a.
- Kein Umbau des Modell-Pickers / der Endpoint-Discovery / der Judge-Picker-Logik
  (`model_picker.js`, `discover_endpoint_models` etc. bleiben).
- Die Routen `/runs/eval`, `/runs/judge`, `/import-bundle`, `/endpoint-models`,
  `/judge-endpoint-models`, `/config-view/…` bleiben **unverändert** (B3a ist
  Template-/Anordnungs-Arbeit).
- Keine neue Vorschau-/Bestätigungs-Stufe vor dem Start (das war Option „Vorschau", nicht
  gewählt) und keine neue Endpoint-Health-Anzeige.

## Architektur

### Seiten-Struktur (`config.html` neu)

Ein Alpine-Scope über der Seite (`x-data`), der die aktive Station hält:

- **Stations-Leiste / Flow-Anzeige:** zeigt die Sequenz **Eval → Judge → Ergebnis** als
  klickbare Marker (Aktions-Namen + Pfeile). Die aktive Station ist aufgeklappt, die anderen
  kompakt.
- **Station „Eval starten":** der bestehende Eval-Block (Pack-Select, Modell-Picker via
  `x-data="modelPicker(...)"`, Eval-starten-Button). Unverändert in Funktion.
- **Station „Judge starten":** der bestehende Judge-Block (Bundle-Select, Judge-Config,
  Judge-Modell-Picker). Mit dem „N Bundle(s) bereit"-Hinweis, wenn `eval_only_bundles`
  nicht leer.
- **„→ Ergebnis ansehen":** kein Formular — ein klarer Verweis/Button zur Übersicht (`/`).
- **Nebeneinstiege (kompakt, unter den Stationen):** Bundle-Import-Formular
  (`/import-bundle`); Resume-Hinweis nur wenn `resume` gesetzt (bestehender Modus).

### Default-Station-Logik (rein client-seitig)

Initialer `active`-Wert aus den vorhandenen Kontext-Daten — **keine neue Route-Logik**:
- `resume` gesetzt → Eval-Station (Resume ist eine Eval-Fortsetzung), Resume-Hinweis sichtbar.
- sonst `eval_only_bundles.length > 0` → Judge-Station aktiv + Hinweis.
- sonst → Eval-Station aktiv.

Die `/config`-Route liefert bereits `eval_only_bundles`, `resume`, `bundle`, `conflict` — B3a
braucht **keine** Änderung an `config_get`.

### Code-Organisation

`config.html` wird umstrukturiert (wächst ggf.) → die Stations-Blöcke in Partials auslagern
(`templates/macros/`, Muster wie B1/B2: `_config_eval.html`, `_config_judge.html`,
`_config_sidesteps.html`), damit keine überladene Datei entsteht. `model_picker.js` unverändert
eingebunden.

## Tests

- **Route/Render (TestClient):** `/config` rendert die drei Flow-Marker (Eval/Judge/Ergebnis)
  + die Stations-Leiste; der Eval- und Judge-Block sind vorhanden; der Ergebnis-Verweis zeigt
  auf `/`.
- **Kontextabhängige Default-Station:** mit ≥1 eval-only-Bundle → der „N Bundle(s) bereit"-
  Hinweis + Judge-Default ist im Markup; ohne → Eval-Default, kein Judge-Hinweis.
- **Resume-Modus:** mit `?resume=<dir>` → Resume-Hinweis sichtbar; ohne → nicht.
- **Funktions-Erhalt:** der Modell-Picker (`modelPicker`-Init, Checkboxen, `models_json`-
  Hidden-Field), der Judge-Picker und das Import-Formular sind weiterhin im Markup
  (Regression-Guard, dass das Umordnen nichts verliert).
- Bestehende `/config`-Tests (`test_gui_config_view.py` o. ä.) bleiben grün bzw. werden auf die
  neue Struktur migriert.

## Risiken & Gegenmaßnahmen

- **Funktion beim Umordnen verloren** (Modell-Picker/Judge-Picker/Import) → expliziter
  Regression-Test, dass alle drei Funktions-Marker im Markup bleiben.
- **`config.html` wird groß** → Partials/Macros (wie B1/B2).
- **Alpine-Init-Reihenfolge:** `model_picker.js` ist bewusst **nicht** deferred (läuft vor
  Alpine-Init) — beim Umstrukturieren diese Script-Reihenfolge beibehalten (Kommentar in
  config.html dokumentiert es).
- **Nav-Nummern-Kollision** mit der Sidebar (1/3/6) → bewusst keine Zahlen im In-Seite-Flow.
