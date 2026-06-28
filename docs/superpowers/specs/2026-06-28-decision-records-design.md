# Spec: Decision Records (ADRs) + Index + Hero

**Datum:** 2026-06-28 · **Branch:** `feat/decision-records` · **Status:** approved

## Problem / Ziel

Die tragenden Entscheidungen des Harness sind heute über ~25 dated Specs/Pläne + 8 Essays in
`docs/explanation/design-decisions.md` verstreut. Es fehlt eine **navigierbare, strukturierte
Entscheidungs-Ebene**, die je Entscheidung **Kontext · Erwogene Alternativen · Auswirkungen** festhält
und auf Spec+Code verlinkt. Zweck (von Johannes formuliert): (1) **Nachvollziehbarkeit der Benchmark-
Ergebnisse**, (2) **Fundament für die künftige Weiterentwicklung**. Adressiert die in AGENTS.md
vermerkte Schuld **CORE-META-03/04** (Hero + volle Diátaxis-Doku). Siehe Memory [[doku-initiative]],
[[bewertungs-methode-transparent]].

## Entscheidungen (aus dem Brainstorming, ratifiziert)

1. **Zuschnitt:** Entscheidungs-Ebene + Index + **Hero/Landing** (Text). Tutorials/How-to/Hero-*Bild* =
   separate Folge-Sub-Projekte (nicht in diesem Spec).
2. **Format:** **ADR-pro-Datei** (MADR-leicht) + Index. Die 8 `design-decisions.md`-Essays wandern als
   ADRs ein.
3. **Abdeckung jetzt:** ~13 ergebnis-/architektur-**tragende** Entscheidungen voll als ADRs; der Index
   führt **alle übrigen** (GUI-Features etc.) als Backlog-Zeilen mit Link auf ihren Spec (Status
   `spec-only`), inkrementell ADR-bar.
4. **`design-decisions.md` bleibt** — als kurzes Verständnis-Narrativ, das die ADRs verlinkt (schützt die
   AGENTS.md- + GUI-Referenz vor toten Links).

## Nicht-Ziele (YAGNI)

- **Keine** Tutorials, How-to-Guides, kein Hero-*Bild* (separate Folge-Projekte).
- **Keine** ADRs für jede der ~25 Entscheidungen jetzt (Backlog im Index).
- **Keine** Code-Verhaltensänderung; nur Doku + AGENTS.md-Pointer (+ Narrativ-Trim).
- **Kein** ADR-Tooling/Generator (handgeschriebene Markdown-Files genügen; YAGNI).

## Design

### Orte & Struktur
- `docs/decisions/NNNN-kebab-titel.md` — ein ADR pro Datei, fortlaufend nummeriert ab `0001`.
- `docs/decisions/README.md` — der **Index/Map** (Tabelle, s. u.).
- `docs/README.md` — der **Hero/Landing** (Diátaxis-Karte + Architektur-Übersicht).

### ADR-Template (MADR-leicht, deutsch)
```markdown
# ADR-NNNN: <Titel>

- **Status:** akzeptiert · <Datum>   (ggf. „ersetzt durch ADR-XXXX" / „verfeinert durch …")
- **Bereich:** <Architektur | Perf-Methodik | Quali-Eval | Judge | GUI | Betrieb>

## Kontext
<Das Problem + die Kräfte/Constraints, die die Entscheidung erzwingen.>

## Entscheidung
<Was entschieden wurde — knapp, präzise.>

## Erwogene Alternativen
- **<Alternative A>** — verworfen, weil <Grund>.
- **<Alternative B>** — verworfen, weil <Grund>.

## Auswirkungen
- Positiv: <Konsequenz>.
- Trade-off / Restgrenze: <bewusst akzeptierter Nachteil>.

## Belege & Links
- Spec: `docs/superpowers/specs/<…>.md` · Code: `touchstone/<…>.py` · Tests: `tests/<…>.py`
- Verwandt: [[ADR-XXXX]]
```

### Index-Format (`docs/decisions/README.md`)
Einleitungssatz + Tabelle, sortiert nach ID; Backlog-Zeilen klar markiert:

| ID | Titel | Status | Bereich | Links |
|---|---|---|---|---|
| 0001 | OpenAI-kompatibel als einzige Schnittstelle | akzeptiert | Architektur | ADR · spec · `client.py` |
| … | … | … | … | … |
| — | Modell-Picker-GUI | spec-only | GUI | spec `2026-06-25-eval-model-picker-single` |

### Abdeckung jetzt — die ~13 tragenden ADRs
1. OpenAI-kompatibel = einzige Schnittstelle (`client.py`).
2. Zwei entkoppelte Producer (Latenz-Runner ↔ Host-Sampler), Merge per Zeitfenster.
3. Verteilung statt Mittelwert (P50/P95, CV%).
4. Modell-Delta vs System-Peak als vergleichbare Speicherzahl (+ Baseline, Unified-Memory-Unschärfe).
5. Warmup verworfen, Cold-Start separat.
6. Quali-Eval-Teilung: `eval` (deterministisch) ↔ `judge` (optional); **Pack = Daten, kein Code**.
7. Master-Dimensionen **holistisch** + Nachvollziehbarkeit via belegte `prompt_id`-Zitate.
8. K.-o./Safety-Gate (zwei Wurzeln: Dim-Boden + red_flag; `red_flag_scope` all/curated).
9. reasoning-only → `unscored` (nicht 1) + Pre-Flight-Smoke; free-default-`max_tokens`.
10. Judge-Runaway-Guard (call_timeout/max_retries=0/degrade/circuit-breaker).
11. **Thinking-Suppression** (`suppress_thinking`, `extra_body`-Hints; à la vault-rag).
12. Judge-Quality-Meta-Eval-Methode (unbiased Referenz, Rubrik, Agreement; + Befunde/A-B).
13. GUI = Out-of-Process-Control-Plane; `runs/` = SSOT (+ transienter Run-Sentinel).
14. Nacht-Queue: ein Modell/Run, reset+settle, Watchdog, `--check` (RAM-Konfund [[multimodel-ram-confound]]).

(14 — die Liste darf im Plan um 1–2 schwanken; Richtwert ~13.)

### Hero/Landing (`docs/README.md`) — Inhalt
Ein Absatz „Was ist touchstone" (thin OpenAI-kompatibler Benchmark-Harness: Perf-Verteilung + Quali-Eval,
gleicher Code M1/M5) · eine **Diátaxis-Karte** (reference=`metrics-and-schema` · explanation=`design-decisions`
· **decisions=`decisions/`** · history=`superpowers/specs`) · eine knappe Architektur-Übersicht (die
Module-Pipeline aus AGENTS.md, verdichtet) · „Wo finde ich…?"-Wegweiser.

### Migration & Referenz-Schutz
- Jeder der 8 `design-decisions.md`-Essays wird zur Kontext/Entscheidung/Auswirkung-Basis seines ADRs
  (ergänzt um **Erwogene Alternativen** + **Status**, die der Essay heute nicht explizit trägt).
- `design-decisions.md` wird auf ein **kurzes Narrativ** getrimmt: 1–2 Sätze je Themenblock + Link auf den
  jeweiligen ADR. Bleibt damit die Diátaxis-„explanation" und die von AGENTS.md/GUI referenzierte Datei.
- **AGENTS.md** „CORE-META-03/04"-Zeile + die `docs/`-Hinweise auf die neue `docs/decisions/`-Struktur
  aktualisieren; die Memory-Doku-Zeiger ([[doku-initiative]]) nach Abschluss nachziehen.

### Wie gebaut + verifiziert
- **Entwurf parallel:** ein Workflow-Fan-out entwirft die ADRs aus ihren jeweiligen Specs (+ `design-
  decisions.md`-Essays) gleichzeitig.
- **Verifikation (adversarial, Controller = ich):** jeder ADR-Entwurf wird gegen **Code + Spec** geprüft —
  kein ADR darf den Code falsch darstellen (Default-Werte, Dateinamen, Verhalten). Falsch-Aussagen werden
  korrigiert, bevor committet wird.
- **Konsistenz-Checks:** alle Index-Links lösen auf (Link-Check-Script); `uv run pytest -q` + `mypy` bleiben
  grün (kein Hot-Path-Code; nur Doku + AGENTS.md-Text).

## Tests / Verifikation (Doku-Projekt)

Prosa hat keine Unit-Tests. Akzeptanz:
1. `docs/decisions/` enthält die ~13 ADRs im Template-Format; jeder hat **nicht-leere** Abschnitte
   Kontext/Entscheidung/Alternativen/Auswirkungen/Links.
2. `docs/decisions/README.md` listet **alle** ADRs **und** Backlog-Zeilen für die übrigen Specs; **alle
   relativen Links lösen auf** (Script `scripts/` oder inline `python`-Check).
3. `docs/README.md` (Hero) existiert mit Diátaxis-Karte + Architektur-Übersicht.
4. `design-decisions.md` getrimmt + verlinkt die ADRs; AGENTS.md-Referenz weiterhin gültig.
5. Jeder ADR-Faktencheck gegen Code bestanden (adversariale Verifikation, keine Fehl-Aussage).
6. `uv run pytest -q` + `uv run mypy touchstone/` + `ruff` unverändert grün.

## Implementierungs-Reihenfolge (Plan, grob)

1. ADR-Template + Index-Gerüst + `docs/decisions/`-Verzeichnis anlegen.
2. ADRs entwerfen (Workflow-Fan-out aus den Specs/Essays).
3. Jeden ADR adversarial gegen Code+Spec verifizieren + korrigieren.
4. Index füllen (tragende ADRs + Backlog-Zeilen) + Link-Check.
5. Hero `docs/README.md` schreiben.
6. `design-decisions.md` auf Narrativ trimmen (Links auf ADRs) + AGENTS.md-Pointer aktualisieren.
7. Volle Suite/mypy/ruff grün; finale Whole-Branch-Review (Linsen: Faktentreue gegen Code, tote Links,
   Doppelungen); Merge nach main + Push (kein PR, Solo).
8. Memory-Doku-Zeiger nachziehen.

## Berührte Dateien

- Neu: `docs/decisions/NNNN-*.md` (~13) · `docs/decisions/README.md` · `docs/README.md`.
- Geändert: `docs/explanation/design-decisions.md` (getrimmt) · `AGENTS.md` (Doku-Pointer/CORE-META).
- Unberührt: `touchstone/` (kein Code), `tests/` (außer evtl. ein Link-Check-Test).

## Risiken / Edge-Cases

- **Fakten-Drift ADR↔Code** (das Hauptrisiko): ein ADR behauptet etwas, das der Code nicht (mehr) tut →
  adversariale Verifikation gegen Code ist Pflicht (Schritt 3), nicht optional.
- **Doppelung** Narrativ↔ADR: `design-decisions.md` trägt nach dem Trim nur noch Verständnis-Narrativ +
  Links, die **Entscheidungs-Detail** (Alternativen/Konsequenzen) lebt allein im ADR (SSOT).
- **Tote Links** im Index/Hero: Link-Check als Akzeptanzkriterium.
- **Scope-Creep** zu Tutorials/How-to: bewusst ausgeschlossen (Nicht-Ziele).
