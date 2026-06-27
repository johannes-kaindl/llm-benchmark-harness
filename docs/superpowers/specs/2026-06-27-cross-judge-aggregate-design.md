# Spec: Cross-Judge-Aggregat (Sub-Projekt 2)

**Datum:** 2026-06-27 · **Branch:** `feat/cross-judge-aggregate` · **Status:** approved

## Problem / Ziel

Sub-Projekt **F** macht den lokalen Judge **bewertbar** und schreibt pro Bundle ein `judge_quality.md`
mit einer **vergleichbaren Headline-Kennzahl** (mean|Δ| zur Cloud-Referenz, Begründungs-Rubrik). F's
**Zweck 1** war ausdrücklich „Judge-Modelle vergleichen" — über Bundles, die von *verschiedenen*
Judge-Modellen bewertet wurden. Heute liegt diese vergleichbare Zahl pro Bundle isoliert herum; es gibt
**keine Aggregat-Ansicht**, die sie nebeneinanderstellt.

Dieses Sub-Projekt liefert das Aggregat: eine `/compare`-artige **Cross-Judge-Qualitätstabelle** im
Web-Control-Center, die `judge_quality.md`-Ergebnisse über Bundles hinweg einsammelt, **nach Pack
gruppiert** und je Pack den besten Judge je Metrik markiert → „welches Modell ist der bessere Judge".

## Vom User entschieden (Brainstorming 2026-06-27)

1. **Reich (Frontmatter erweitern):** F's `render_judge_quality_md`-Frontmatter wird um die 3 fehlenden
   Rubrik-Raten erweitert, sodass die Tabelle alle 4 Rubrik-Dimensionen + mean|Δ| zeigt. Kleine,
   rückwärtskompatible F-Änderung.
2. **Eigene Route/View:** ein dediziertes `/compare-judges` mit eigener Tabelle (nicht eine Sektion auf
   `/compare`) — Judges vergleichen ist eine andere Achse als Hardware/Modelle vergleichen.

## Die eine echte Architektur-Wahl: Frontmatter-Parsen (kein separates Artefakt)

- **Gewählt:** Quelle ist das pro Bundle persistierte `judge_quality.md` (das Meta-Eval-Ergebnis-Artefakt
  = SSOT). Eine neue pure Funktion parst dessen YAML-Frontmatter. Damit die Zeile self-contained ist,
  trägt das Frontmatter alles Nötige: `bundle`, `pack`, `judge_model` + die 5 Zahlen.
- **Verworfen:** ein separates `judge_quality.json` neben dem `.md` schreiben — mehr Artefakte +
  Allowlist-/Ledger-Pflege. Frontmatter-Parsen der bestehenden `.md` ist leaner (kein neues Artefakt,
  keine Allowlist-Änderung).

## Die Routing-Falle: `/compare-judges`, nicht `/compare/judges`

Die bestehende Route `@app.get("/compare/{name}")` (`app.py:385`, redirect → `/result/{name}`) würde
`/compare/judges` als `name="judges"` abfangen (→ redirect → `/result/judges` → 404). Die neue Route
heißt daher **flach `/compare-judges`** und umgeht die Path-Param-Verschattung vollständig.

## Nicht-Ziele (v1, YAGNI)

- **Kein** Checkbox-Selektion+Diff-Widget wie auf `/compare`. Die Pack-Gruppierung + Winner-Highlight
  liefern den Vergleich direkt; eine N-aus-M-Selektion ist späteres Enhancement.
- **Kein** CSV/MD-Export der Judge-Tabelle (v1 ist die On-Screen-Ansicht).
- **Kein** neuer Sidebar-Stations-Eintrag — Einstieg per Link auf `/compare`.
- **Keine** Cross-Pack-Vergleiche (nur innerhalb gleichem Pack valide — s. Vergleichbarkeit).
- **Keine** SP3-Themen (mehr Packs, K.-o.-Dimensions-Outlier-Betonung).
- **Kein** Re-Derive aus der Cloud-Antwort — die gefüllte `judge_meta_response.yaml` wird vom GUI-Ingest
  nicht persistiert; das Frontmatter des `judge_quality.md` ist die belastbare Quelle.

## Vergleichbarkeit (von mir entschieden)

F's Headline ist nur **innerhalb gleichem Pack + gleicher Cloud-Referenz** valide. Die Cloud-Referenz ist
nicht erfasst (externe KI, nicht protokolliert) → **Pack** ist die belastbare erfasste Achse. Die Tabelle
**gruppiert nach Pack**; pro Gruppe ein Kurz-Hinweis „vergleichbar nur bei gleichem Pack + gleicher
Cloud-Referenz". Winner-Markierungen werden **nur innerhalb einer Pack-Gruppe** berechnet.

## Design

### F-Frontmatter-Enrichment — `render_judge_quality_md` (touchstone/gui/judge_meta.py)

Das Frontmatter (heute 5 Zeilen) wird um `pack` + 3 Rubrik-Raten erweitert (alle additiv, alte Reader
ignorieren Unbekanntes, alte Dateien fehlen die Felder → der Parser liefert `None`):

```yaml
---
type: "judge_quality"
bundle: <run_dir.name>
pack: <detail["pack"].id>                 # NEU
judge_model: "<model>"
mean_abs_delta: <float|null>
names_improvement_rate: <float|null>
cites_evidence_rate: <float|null>         # NEU = round(n/t, 2) if t else null
justifies_level_rate: <float|null>        # NEU
catches_safety_rate: <float|null>         # NEU
---
```

Die 3 neuen Raten kommen aus `rubric.cites_evidence` / `.justifies_level` / `.catches_safety` (je
`tuple[int,int]`), gerundet wie `names_improvement_rate` (`round(n/t, 2) if t else null`). **Body
unverändert** (die Raten stehen dort schon als `_pct(...)`-Text — nur das Frontmatter wächst).

### Pure Logik — `touchstone/gui/judge_compare.py` (neu, server-frei)

```python
def parse_frontmatter(text: str) -> dict[str, Any] | None
    # Splittet einen führenden ---\n…\n----Block; yaml.safe_load; None wenn kein
    # Frontmatter / kein Mapping / Parse-Fehler. Der bisher fehlende Parser, tolerant.

@dataclass
class JudgeQualityRow:
    bundle: str
    pack: str
    judge_model: str
    mean_abs_delta: float | None
    names_improvement_rate: float | None
    cites_evidence_rate: float | None
    justifies_level_rate: float | None
    catches_safety_rate: float | None
    @property
    def key(self) -> str: return f"{self.bundle}|{self.judge_model}"

def judge_quality_rows(runs_dir: Path) -> list[JudgeQualityRow]
    # runs_dir.rglob("judge_quality.md") → parse_frontmatter; überspringt Dateien ohne
    # Frontmatter oder mit type != "judge_quality" (malformed → skip, nie Crash). Fehlende
    # Raten-Keys → None. Deterministisch sortiert (pack, bundle, judge_model).

@dataclass
class PackGroup:
    pack: str
    rows: list[JudgeQualityRow]
    winners: dict[str, str | None]   # metric → row.key des eindeutigen Siegers (oder None)

def group_by_pack(rows: list[JudgeQualityRow]) -> list[PackGroup]
    # Gruppiert nach pack; je Gruppe winners: mean_abs_delta niedriger=besser, die 4 Raten
    # höher=besser; None aus dem Vergleich ausgeschlossen; Gleichstand → None (keine Trophäe,
    # wie aggregate.diff_rows). Gruppen nach pack sortiert.
```

- **Winner-Mathematik (pur):** je Pack-Gruppe, je Metrik der Extremwert über die Zeilen mit
  vorhandenem (nicht-`None`) Wert; nur ein **eindeutiger** Leader bekommt `row.key`, sonst `None`.
  `mean_abs_delta` minimiert (näher an der Referenz = besser), die 4 Raten maximiert.

### Route — `GET /compare-judges` (touchstone/gui/app.py, in create_app())

```python
@app.get("/compare-judges", response_class=HTMLResponse)
def compare_judges(request: Request) -> HTMLResponse:
    from touchstone.gui import judge_compare
    groups = judge_compare.group_by_pack(judge_compare.judge_quality_rows(runs_dir))
    return render("compare_judges.html", request, groups=groups, active="compare")
```

Read-only, kein User-Path-Param → kein Confinement-Thema (scannt nur `runs_dir`). Lazy-Import wie die
übrigen GUI-Routen.

### Template — `compare_judges.html` (neu) + Link auf `/compare`

- Je `PackGroup`: Pack-Überschrift + Vergleichbarkeits-Hinweis + Tabelle mit Spalten
  **Bundle · Judge-Modell · mean|Δ| · Verbesserung benannt % · Belege % · Höhe begründet % · Sicherheit %**.
  Raten sind Brüche 0..1 → als `%` gerendert (`{{ (r.x * 100) | round }}%`, `None` → „—"); `mean_abs_delta`
  als Float (`None` → „—"). 🏆 bei `r.key == group.winners[metric]`.
- Leerzustand (keine `judge_quality.md` gefunden) → freundliche Meldung + Hinweis auf den judge-meta-Flow.
- `compare.html` bekommt einen Link „Judge-Qualität vergleichen →" auf `/compare-judges`.

## Datenfluss

```
judge_quality.md (pro Bundle, angereichertes Frontmatter)
  → judge_compare.judge_quality_rows(runs_dir)        [rglob + parse_frontmatter, malformed→skip]
  → judge_compare.group_by_pack(rows)                 [nach Pack gruppiert + Winner je Metrik]
  → compare_judges.html                               [gruppierte Tabelle, 🏆 je Pack-Gruppe]
```

## Fehlerbehandlung (never-500)

- `judge_quality.md` mit malformem/fehlendem Frontmatter → `parse_frontmatter` liefert `None` → Zeile
  **übersprungen** (nie Crash).
- `type != "judge_quality"` (eine fremde `.md` namens judge_quality) → übersprungen.
- Fehlende angereicherte Felder (alte, vor diesem SP erzeugte Dateien) → Raten/`pack` = `None`/Default →
  Spalte „—" (für `pack` fehlend: Gruppe „(unbekannt)" oder Skip — s. Risiken).
- Kein `judge_quality.md` vorhanden → leere `groups` → Template-Leerzustand.
- Winner mit nur `None`-Werten in einer Spalte → kein Sieger.

## Tests (TDD, RED→GREEN)

**Pur `judge_compare.py`** (`tests/test_judge_compare.py`):
- `parse_frontmatter`: gültiger `---`-Block → dict; kein Frontmatter → None; malformed YAML → None;
  Nicht-Mapping → None.
- `judge_quality_rows`: zwei synthetische `judge_quality.md` (verschiedene Bundles/Judges/Packs) unter
  einem tmp runs_dir → 2 Zeilen mit korrekten Feldern; eine kaputte Datei dazwischen → übersprungen,
  Rest geliefert; `type != judge_quality` → übersprungen; fehlende Raten-Keys → `None`.
- `group_by_pack`: Zeilen aus 2 Packs → 2 Gruppen; winners je Metrik korrekt (niedrigstes mean|Δ| /
  höchste Rate gewinnt); **Gleichstand → None**; eine Spalte ganz `None` → kein Sieger; Winner nur
  innerhalb der Pack-Gruppe.

**Route** (`tests/test_gui_compare_judges.py`): `/compare-judges` rendert die gruppierte Tabelle (Pack-
Überschrift + Judge-Modelle sichtbar); Leerzustand bei leerem runs_dir; tolerant bei einer kaputten
`judge_quality.md` (200, kein 500). Muster: `_client(tmp_path)`-Helfer wie `test_gui_judge_meta_surface`.

**F-Enrichment** (`tests/test_judge_meta.py` erweitern): `render_judge_quality_md` schreibt jetzt
`pack:` + `cites_evidence_rate:` + `justifies_level_rate:` + `catches_safety_rate:` ins Frontmatter,
korrekt aus der `RubricSummary` gerundet; und `parse_frontmatter` des erzeugten Outputs liefert alle
Felder (Round-Trip-Bindeglied F↔SP2).

**Gate:** `pytest -q` + `mypy touchstone/` (strict) + `ruff check . && ruff format --check .`.

**Headless-Smoke** (GUI neu starten): echte Bundles → (nötigenfalls 1–2 via judge-meta-Ingest mit
angereichertem Renderer neu erzeugen) → `/compare-judges` zeigt die nach Pack gruppierte Tabelle mit
🏆-Markierungen; alte `judge_quality.md` ohne neue Felder → „—" statt Crash.

**Adversariale Whole-Branch-Review** vor Merge (3 Linsen, Controller verifiziert jeden Fund):
(1) `parse_frontmatter`/`judge_quality_rows` never-crash bei beliebigem `.md`-Müll; (2) Winner-Mathematik
(min vs max je Metrik, None-Ausschluss, Gleichstand→keine Trophäe, nur innerhalb Pack); (3)
Frontmatter-Enrichment rückwärtskompatibel (alte Dateien degradieren, Body unverändert) + F-Body-Bytes
nicht versehentlich verändert.

## Implementierungs-Reihenfolge (Plan)

1. **F-Frontmatter-Enrichment** in `render_judge_quality_md` (+`pack` +3 Raten) + Test in
   `test_judge_meta.py`. (Zuerst, damit `judge_compare` gegen das echte Format testen kann.)
2. **Pure `judge_compare.py`**: `parse_frontmatter` + `JudgeQualityRow` + `judge_quality_rows` + Tests.
3. **`group_by_pack` + winners** (pur) + Tests (der Vergleichs-Kern).
4. **Route `/compare-judges`** + `compare_judges.html` + Link auf `/compare` + Route-Test.
5. Volle Suite + `mypy` + `ruff` (check & format) + Headless-Smoke (GUI neu starten).
6. Adversariale Review → bestätigte Funde fixen → Merge nach `main` + Push (kein PR, Solo-Repo).

## Berührte Dateien

- `touchstone/gui/judge_meta.py` — Frontmatter-Enrichment in `render_judge_quality_md` (+4 Zeilen,
  Body unverändert).
- `touchstone/gui/judge_compare.py` **(neu)** — parse_frontmatter, JudgeQualityRow, judge_quality_rows,
  PackGroup, group_by_pack/winners.
- `touchstone/gui/app.py` — `GET /compare-judges` in `create_app()`.
- `touchstone/gui/templates/compare_judges.html` **(neu)** + Link in `compare.html`.
- `tests/test_judge_compare.py` **(neu)** · `tests/test_gui_compare_judges.py` **(neu)** ·
  `tests/test_judge_meta.py` (erweitert).

## Risiken / Edge-Cases

- **Alte `judge_quality.md` ohne `pack`-Frontmatter:** sie haben kein `pack` → Gruppierung. Entscheidung:
  Zeilen ohne `pack` werden unter einer Gruppe **„(Pack unbekannt — vor SP2 erzeugt, neu einlesen)"**
  geführt (sichtbar, nicht still verschluckt), Raten dort „—". So bleibt die alte Datei sichtbar mit
  klarer Handlungsaufforderung (re-ingest) statt zu verschwinden.
- **Frontmatter-Parser-Robustheit:** beliebiger `.md`-Inhalt darf nie crashen → `parse_frontmatter` fängt
  alle YAML-Fehler und liefert `None`; Test mit Müll-Input.
- **F-Body-Bytegleichheit:** das Enrichment fügt nur Frontmatter-Zeilen hinzu; ein Test prüft, dass die
  Body-Sektionen (Headline + 3 Abschnitte) unverändert bleiben (kein versehentlicher Eingriff in den
  bestehenden `render_judge_quality_md`-Body, der von SP1-Tests/Smoke abhängt).
- **Raten-Einheit:** Frontmatter speichert Brüche 0..1 (wie `names_improvement_rate` heute); das Template
  rendert `× 100` als „%". Konsistent in einer Stelle dokumentiert, damit niemand doppelt skaliert.
