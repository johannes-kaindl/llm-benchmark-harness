# Spec: Pack-Editor (Guided YAML)

**Datum:** 2026-06-25 · **Branch:** `feat/gui-pack-editor` · **Status:** approved

## Problem / Ziel

`pack.html` ist heute ein reiner **Viewer**. B3b macht daraus einen **Editor**: ein Pack
laden/sehen/anpassen/exportieren — in-browser, ohne Terminal-Umweg. Ein Pack ist handgeschriebenes
YAML mit tiefer, verschachtelter Struktur (scale · dimensions+Gewichte · ko_rule · prompt_variants ·
sampling · categories→prompts mit green/red-Flags · Rubrik · safety_critical/format_strict). Statt
eines riesigen, fragilen Formulars: ein **YAML-Editor mit starkem Sicherheitsnetz** —
Server-Validierung über die *eine* pydantic-Schicht + Live-Vorschau, die den Viewer recycelt.

## Nicht-Ziele

- Kein Formular-Editor für die verschachtelte Struktur (YAGNI; Packs sind YAML, der User schreibt sie von Hand).
- Keine Client-seitige Validierung (würde `pack.py`-Regeln duplizieren → Drift). Validierung ist **immer** server-seitig.
- Keine Änderung an `pack.py` (Datenkontrakt + Validierung bleiben die Quelle der Wahrheit).
- Keine Migration/Versionierung bestehender Bundles. Editieren ändert die **Quelle für künftige
  Läufe**; bestehende Bundles sind self-contained und bleiben unberührt.

## Design

### Pure Logik — `touchstone/gui/packs.py` (neues Modul, spiegelt `configs.py`)

```python
def validate_pack_yaml(text: str) -> dict[str, Any]:
    """Parse + validate pack YAML. Pure (text in, no file I/O). NEVER raises.
    {ok: bool, errors: [{loc: str, msg: str}], summary: {...}|None, pack: Pack|None}."""
```
- `yaml.safe_load` → `Pack.model_validate`. On success `pack` is the validated `Pack` (so the
  route renders the preview without re-validating); `pack=None` on any failure. The route returns
  `{ok, errors, summary, preview_html}` — `pack` stays server-side, never in the JSON.
- YAMLError → `ok=False`, `errors=[{loc:"(yaml)", msg:<parse error>}]`.
- ValidationError → `ok=False`, `errors` aus `e.errors()` (loc als dotted path, msg).
- Nicht-Mapping/leer → `ok=False` mit klarer Meldung.
- Erfolg → `ok=True`, `summary={prompts, dimensions, variants, categories, max_weighted}` (aus dem
  validierten `Pack`: `len(all_prompts())`, `len(dimensions)`, `len(prompt_variants)`,
  `len(categories)`, `max_weighted()`).

```python
def safe_pack_filename(name: str) -> str | None:
    """Confine a save target to packs/<name>.yaml. Returns the bare 'name.yaml' or None.
    Accept ^[A-Za-z0-9_-]+(.yaml)?$ ; reject traversal, separators, other extensions, empty."""
```

`NEW_PACK_TEMPLATE: str` — eine **minimal valide** Pack-YAML (1 Dimension, ko_rule darauf, variant
`none`, 1 Kategorie/1 Prompt) als Startpunkt für „+ Neues Pack".

### Template-Refactor — Vorschau recycelt den Viewer

- Den Pack-Body (Dimensionen-, Varianten-, Prompts-Karten) aus `pack.html` nach
  `macros/_pack_body.html` extrahieren (nimmt `pack`, nutzt `g`).
- `pack.html` bindet das Partial ein (behält Breadcrumb/Header/Description/Method-Explainer) **und**
  bekommt einen „✎ Bearbeiten"-Link → `/pack-editor?path=<datei>`.
- Die Editor-Vorschau rendert **nur** `_pack_body.html` zu einem HTML-String
  (`_templates.get_template(...).render(pack=pk, g=_glossary.describe)` — kein `request` nötig).

### Routen — `touchstone/gui/app.py`

- `GET /pack-editor` (`?path=` optional) → rendert `pack_editor.html`. Mit gültigem `?path`
  (confined auf `packs/*.yaml`-Glob, wie der Viewer) → **rohen YAML-Text** der Datei in die Textarea;
  sonst `NEW_PACK_TEMPLATE`. Übergibt die editierbaren Pack-Dateien für ein „Laden"-Dropdown.
- `POST /packs/validate` (Form `yaml_text`) → `validate_pack_yaml` → JSON
  `{ok, errors, summary, preview_html}`. Bei `ok` ist `preview_html` das gerenderte `_pack_body.html`,
  sonst `null`. **Nie 500** (alles gefangen). Größen-Cap auf `yaml_text` (z. B. 1 MB).
- `POST /packs/save` (Form `filename`, `yaml_text`, `overwrite`) →
  `safe_pack_filename` (sonst 400) · `validate_pack_yaml` muss `ok` sein (sonst 400, **nie ungültig
  schreiben**) · Pfad `packs/<name>.yaml` resolved + `is_relative_to(packs/)` · existiert & nicht
  `overwrite` → **409** · sonst schreiben → `{saved: "<name>.yaml"}`.
- CSRF/Origin-Guard + TrustedHost greifen automatisch auf die POSTs (bestehende Middleware).
- **Download = client-seitig** (Blob der Textarea, verbatim — behält Kommentare/Anker/Reihenfolge);
  kein Server-Round-Trip, keine Validierungs-Hürde für einen Entwurf. Nur **Speichern** validiert.

### Frontend — `pack_editor.html` + `static/pack_editor.js`

Alpine `packEditor`: `{files, path, yamlText, ok, errors, summary, previewHtml, saving, saveError}`.
- Textarea `x-model="yamlText"`; „Laden"-Dropdown (`files`) navigiert zu `/pack-editor?path=`.
- „Validieren" → POST `/packs/validate` → setzt `ok/errors/summary/previewHtml`. Außerdem
  validate-on-load (init). (Optional später: debounced Auto-Validate.)
- Vorschau-Pane `x-html="previewHtml"` bei `ok`; sonst Fehlerliste (`loc · msg`) + Summary-Zeile.
- „Herunterladen" → Blob-Download von `yamlText`.
- „In packs/ speichern" → Dateiname-Feld (vorbelegt aus `path` oder neuer Name) → POST `/packs/save`;
  bei 409 → Overwrite-Bestätigung → erneut mit `overwrite=true`. Save `:disabled` solange `!ok`.
- `pack_editor.js` **non-deferred** laden (wie `model_picker.js`), damit `packEditor` vor
  `alpine:init` registriert ist.

## Tests (TDD)

**Pure `validate_pack_yaml`** (`tests/test_gui_packs.py`):
- valide YAML → `ok`, Summary-Zähler korrekt.
- Parse-Fehler (kaputte YAML) → `ok=False`, Fehler mit Parse-Meldung.
- Schema-Verstöße je eigener Test: scale ≠ 1..5 · dangling `ko_rule.dimension` · doppelte prompt-id ·
  `weight ≤ 0` → `ok=False`, `errors` tragen `loc`+`msg`.
- Nicht-Mapping/leer → `ok=False`.

**Pure `safe_pack_filename`**: `"foo"→"foo.yaml"` · `"foo.yaml"→"foo.yaml"` · `"../x"/"a/b"/""/"x.yml"/Sonderzeichen → None`.

**Routen** (`tests/test_gui_pack_editor.py`, TestClient; Save-Tests `monkeypatch.chdir(tmp_path)` +
`mkdir packs`):
- `GET /pack-editor` rendert; `?path=packs/ndassist.yaml` lädt rohen YAML-Text; ohne path → Template.
- `POST /packs/validate` valid → `ok`, `preview_html` enthält Pack-Marker (z. B. „Dimensionen").
- `POST /packs/validate` invalid → `ok=False`, `errors` nicht leer, `preview_html=null`.
- `POST /packs/save` valides neues Pack → Datei unter `packs/` geschrieben, `{saved}`.
- `POST /packs/save` invalide YAML → 400, **kein** Write.
- `POST /packs/save` bad filename (`../x`) → 400/404, kein Write.
- `POST /packs/save` existierend ohne `overwrite` → 409; mit `overwrite=true` → 200, überschrieben.
- Pfad-Guards/Traversal auf `/pack-editor?path=`.

**Bestehende Tests:** `test_gui_pack_view.py` muss grün bleiben (Refactor 1:1 — Viewer rendert
weiterhin Dimensionen/Varianten/Prompts). „Bearbeiten"-Link additiv.

**Headless-Smoke** (Memory `gui-restart-after-changes`): frischer Server, `/pack-editor` lädt 200 mit
Editor; `/packs/validate` (valid + invalid) liefert erwartete Shape; serviertes `pack_editor.js`
identisch zur Datei.

## Implementierungs-Reihenfolge (Plan)

1. Pure `packs.py` (`validate_pack_yaml`, `safe_pack_filename`, `NEW_PACK_TEMPLATE`) + Unit-Tests (RED→GREEN).
2. Template-Refactor `_pack_body.html` + `pack.html`-Include + „Bearbeiten"-Link; `test_gui_pack_view` grün halten.
3. Routen `/pack-editor`, `/packs/validate`, `/packs/save` + Route-Tests (RED→GREEN).
4. `pack_editor.html` + `pack_editor.js` + Template-Marker-Tests.
5. Volle Suite + mypy + ruff (check & format) + Headless-Smoke.
6. Adversariale Whole-Branch-Review → bestätigte Funde fixen → Merge nach main + Push.

## Berührte Dateien

- `touchstone/gui/packs.py` (neu) · `touchstone/gui/app.py` (3 Routen)
- `touchstone/gui/templates/macros/_pack_body.html` (neu) · `pack.html` (Include + Link)
- `touchstone/gui/templates/pack_editor.html` (neu) · `touchstone/gui/static/pack_editor.js` (neu)
- `tests/test_gui_packs.py` (neu) · `tests/test_gui_pack_editor.py` (neu)

## Risiken / Edge-Cases

- **Ungültiges Pack nie schreiben** — Save validiert hart vor dem Write.
- **Pfad-Confinement** — `safe_pack_filename` + `is_relative_to(packs/)`, wie der `import-bundle`-Write.
- **Großer YAML-Body** — Größen-Cap auf `yaml_text`.
- **Editieren eines schon gelaufenen Packs** — bestehende Bundles bleiben unberührt (self-contained);
  Editor weist darauf hin, `version`-Bump liegt beim User.
- **`g`-Glossar in der Vorschau** — Partial via Templates-Env gerendert, `g` ist Env-global → verfügbar.
