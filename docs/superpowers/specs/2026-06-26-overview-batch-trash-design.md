# Spec: Übersicht-Batch — Export + Löschen-in-Papierkorb (Sub-Projekt D)

**Datum:** 2026-06-26 · **Branch:** `feat/gui-overview-batch-trash` · **Status:** approved

## Problem / Ziel

Die Übersicht (`/`) listet alle Läufe, aber es gibt **keine Batch-Aktion**: alte Smoke-/Crashed-/
Testläufe lassen sich nicht wegräumen, und man kann nicht mehrere Läufe auf einmal exportieren.
D fügt **Haken pro Zeile** + eine Aktionsleiste hinzu: ausgewählte Läufe als **Zip exportieren** oder
**in den Papierkorb verschieben** (reversibel). Die Checkbox-/Footer-Mechanik wird 1:1 von der
`/compare`-Seite übernommen, die das schon kann.

## Nicht-Ziele (v1, YAGNI)

- Kein per-Item-Permanentlöschen aus dem Papierkorb (nur „alles leeren").
- Keine Größen-/Speicherplatz-Anzeige, kein Hintergrund-Lösch-Queue.
- Kein Meta-Report (das ist Sub-Projekt E, eigene Spec).
- Keine Änderung an der Mess-/Bundle-Logik.

## Design

### Auswahl (Übersichts-Tabelle, Muster aus `compare.html`)

- Tabelle in `x-data="{ sel: [] }"` wrappen. Linke Checkbox-Spalte je Zeile:
  `<input type="checkbox" :value="…" x-model="sel">` mit `value="{{ b.run_dir.name }}"`.
- Sticky-Aktionsleiste `x-show="sel.length"`: „**N ausgewählt** · [Exportieren] [Löschen]" +
  „Auswahl aufheben". Beide Aktionen sind Formulare (`names` = gewählte `sel`-Werte).
- Auswahl-Schlüssel = `run_dir.name` (stabil; server-seitig je Name per `_confine` abgesichert).
- Inline-Alpine (kein neues JS-File nötig — wie `/compare`).

### Pure Logik — `touchstone/gui/trash.py` (neu)

```python
TRASH_DIRNAME = ".trash"

def trash_dir(runs_dir: Path) -> Path:            # runs_dir/.trash
def move_to_trash(run_dir: Path, runs_dir: Path) -> Path:
    """Move run_dir → runs_dir/.trash/<name> (collision-safe __2/__3…). Returns the dest."""
def restore_from_trash(name: str, runs_dir: Path) -> Path:
    """Move runs_dir/.trash/<name> → runs_dir/<name> (collision-safe). Returns the dest."""
def list_trash(runs_dir: Path) -> list[str]:      # sorted names under .trash (dirs only)
```
- Confinement: alle drei resolven + prüfen `is_relative_to` (Quelle bzw. Ziel), nie Symlink-Escape.
- Kollisionssicher: existiert das Ziel, Suffix `__2`, `__3`, … (wie `import_bundle`).

### Discovery-Änderung — `bundles.discover`

`discover` iteriert `runs_dir`-Kinder; künftig **Dot-Verzeichnisse überspringen** (`name.startswith(".")`),
damit `.trash` nicht als (kaputter) Lauf erscheint. (Heute liefert `classify(.trash)` zwar `None`, aber
die Filterung ist explizit + günstig.)

### Routen — `touchstone/gui/app.py`

1. **`POST /runs/batch-delete`** (`names: list[str] = Form(...)`): pro Name → `_confine` ·
   wenn `is_active(read_sentinel(dir))` → **überspringen** (Live-Run nie wegräumen) · sonst
   `trash.move_to_trash`. Leere/komplett-ungültige Auswahl → 400. Danach **PRG-Redirect 303 → `/`**
   (getrashte Läufe verschwinden aus der Liste; übersprungene bleiben sichtbar).
2. **`POST /runs/batch-export`** (`names: list[str] = Form(...)`): **ein** Zip, jeder gewählte Lauf
   unter `arcname=<run_name>/<file>` (Ledger-Liste wie `/export-bundle`:
   `bundle.json, responses.jsonl, scores.csv, reports.jsonl, judgements.jsonl, scorecard.md, perf.csv,
   resources.jsonl`). Confine je Name; nicht existierende/leere Läufe still überspringen. Antwort:
   `application/zip` + `Content-Disposition: attachment; filename="touchstone-export-<N>-runs.zip"`
   → Browser lädt, bleibt auf der Seite. Leere Auswahl → 400.
3. **`GET /trash`** → `trash.html`: listet `list_trash(runs_dir)` mit je **[Wiederherstellen]**
   (`POST /trash/restore`, `name`) und global **[Papierkorb leeren]** (`POST /trash/purge`).
4. **`POST /trash/restore`** (`name: str = Form(...)`): `restore_from_trash` → PRG 303 → `/trash`.
5. **`POST /trash/purge`**: hartes `shutil.rmtree(trash_dir)` (neu anlegen leer) — **strikt auf
   `runs/.trash/` confined**, sonst nichts. PRG 303 → `/`.

### Übersicht-Indikator

Wenn `list_trash` nicht leer: kleine Zeile „🗑 Papierkorb: N · [ansehen](/trash)" über/unter der Tabelle.

## Sicherheit (entschieden)

- Jeder Name einzeln `_confine` (resolve + `is_relative_to(runs_dir)`); Client-Array nie blind nutzen.
- `is_active`-Läufe (zombie-aware via psutil) sind nicht löschbar → übersprungen.
- Purge operiert **ausschließlich** auf `runs/.trash/` (eigener Confine-Check).
- CSRF/Origin-Guard + TrustedHost greifen automatisch auf allen POSTs.
- Move/Restore kollisionssicher + symlink-confined (resolve + `is_relative_to`).
- Pro-Run-Ergebnis statt alles-oder-nichts; Partial-Failure bricht den Batch nicht ab.

## Tests (TDD)

**Pure `trash.py`** (`tests/test_gui_trash.py`): move_to_trash (verschiebt, legt `.trash` an,
kollisionssicher `__2`) · restore_from_trash (zurück, kollisionssicher) · list_trash (nur Dirs,
sortiert, leer wenn kein `.trash`) · Confinement (Traversal/Symlink → Fehler, kein Escape).

**Routen** (`tests/test_gui_batch_trash.py`, TestClient; `monkeypatch.chdir` n. n. — runs_dir ist
injiziert): batch-delete verschiebt gewählte nach `.trash` + 303→`/` · überspringt `is_active`-Run
(bleibt) · leere/ungültige `names` → 400 · batch-export liefert Zip mit `<name>/bundle.json` der
Gewählten, `application/zip`, leere Auswahl → 400 · `/trash` listet getrashte · restore holt zurück ·
purge leert nur `.trash` (ein paralleler echter Lauf in `runs/` bleibt) · Traversal in `names`/`name`
→ abgewiesen, kein Escape.

**Discovery** (`tests/test_gui_bundles.py`): `discover` überspringt `.trash`/Dot-Dirs.

**Übersicht-Marker** (`tests/test_gui_*`): Checkbox-Spalte (`x-model="sel"`) + Aktionsleiste
(`batch-delete`/`batch-export`) + Papierkorb-Indikator nur wenn nicht leer.

**Headless-Smoke** (Memory `gui-restart-after-changes`): frischer Server; in einem Temp-`runs/`
einen Dummy-Lauf anlegen → batch-delete → verschwindet aus `/`, taucht in `/trash` auf → restore →
zurück; `.trash` nie als Lauf gelistet.

**Adversariale Whole-Branch-Review** vor Merge — Fokus auf die neue **Lösch-/Purge-Capability**
(Confinement, `is_active`-Guard, dass Purge nichts außerhalb `.trash` trifft).

## Implementierungs-Reihenfolge (Plan)

1. Pure `trash.py` + Unit-Tests (RED→GREEN).
2. `discover` Dot-Dir-Filter + Test.
3. Routen batch-delete/batch-export/trash-view/restore/purge + Route-Tests.
4. `overview.html` Checkboxen + Aktionsleiste + Indikator; `trash.html`.
5. Volle Suite + mypy + ruff (check & format) + Headless-Smoke.
6. Adversariale Review → bestätigte Funde fixen → Merge + Push.

## Berührte Dateien

- `touchstone/gui/trash.py` (neu) · `touchstone/gui/app.py` (5 Routen) · `touchstone/gui/bundles.py`
  (discover-Filter) · `templates/overview.html` (Checkboxen + Leiste + Indikator) ·
  `templates/trash.html` (neu) · `tests/test_gui_trash.py` + `tests/test_gui_batch_trash.py` (neu) +
  bestehende bundles/overview-Tests ergänzt.

## Risiken / Edge-Cases

- **Auswahl wird stale** (ein Lauf endet/crasht zwischen Laden und Submit): batch-delete überspringt
  fehlende/aktive sauber (pro-Run-Ergebnis), kein Hard-Fail.
- **`.trash` kollidiert mit einem Lauf namens `.trash`**: ausgeschlossen — Lauf-Namen sind Zeitstempel,
  und Dot-Dirs sind aus Discovery/Confine ausgenommen.
- **Symlink-Run-Dir**: move/restore resolven + confinen (wie die gehärteten Reader).
- **Großer Export** (multi-GB `responses.jsonl`): synchron, aber lokal/Einzelnutzer — akzeptabel v1.
