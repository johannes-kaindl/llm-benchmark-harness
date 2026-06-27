# Spec: Judge-Meta GUI-Oberfläche (Sub-Projekt 1)

**Datum:** 2026-06-27 · **Branch:** `feat/judge-meta-gui` · **Status:** approved

## Problem / Ziel

Sub-Projekt **F** lieferte die Judge-Qualitäts-Meta-Eval **CLI-only** aus: `touchstone judge-meta
export|ingest` erzeugt aus einem bewerteten Bundle ein Anfrage-Dokument für eine externe Cloud-KI und
liest deren ausgefüllte YAML-Antwort zu `judge_quality.md` ein. Der ganze Round-Trip ist heute nur im
Terminal erreichbar — im Web-Control-Center (Station „Ergebnis") existiert er nicht.

Dieses Sub-Projekt **flächt F im GUI an**, exakt wie E ein Follow-up zu D war: eine neue Karte auf der
Per-Bundle-Ergebnisseite, über die man (1) Anfrage-MD + leere Antwort-Vorlage herunterlädt, (2) die von
der Cloud-KI ausgefüllte YAML-Antwort **einfügt** und auswertet, (3) das Ergebnis als Inline-Summary sieht
und `judge_quality.md` herunterlädt.

**Rein additiv:** die gesamte F-Logik in `touchstone/gui/judge_meta.py` (`render_request_md`,
`empty_response_template`, `parse_meta_response`, `compute_agreement`, `aggregate_rubric`,
`render_judge_quality_md`) wird **unverändert** wiederverwendet. Es entsteht **kein** neuer Renderer und
**keine** Änderung an F's Ausgaben.

## Vom User entschieden (Brainstorming 2026-06-27)

1. **Ingest-Input = Einfügen (Textarea)** — gespiegelt am etablierten Pack-Editor (`/packs/validate` +
   `/packs/save`), nicht Datei-Upload. `copy-from-cloud-chat → paste` ist der natürliche Fluss.
2. **K.-o.-Dimensions-Outlier-Betonung = deferred zu Sub-Projekt 3** — `render_judge_quality_md` bleibt in
   diesem Build unangetastet (kleinerer, fokussierter Spec, null Risiko für F's Output).
3. **Nach Ingest = Inline-Summary + Download** — Headline-Zahlen + Agreement-Kurzüberblick direkt aus den
   berechneten Objekten (`AgreementResult`/`RubricSummary`), plus Download-Button. Kein MD→HTML-Renderer.

## Die eine echte Architektur-Entscheidung: Export = Stream-only

- **Gewählt:** Die Export-Routen erzeugen Anfrage-MD + leere YAML-Vorlage **on-the-fly** und streamen sie
  als Download (spiegelt E's `/export-meta-report` / `/export-meta-csv`). Es wird **nichts** auf Disk
  geschrieben, bis der Ingest `judge_quality.md` schreibt.
- **Verworfen:** CLI-Parität (die CLI `export` schreibt `judge_meta_request.md` + `judge_meta_response.yaml`
  ins Bundle). **Grund:** GET bleibt idempotent, und Stream-only eliminiert die F-Falle *„`export`
  überschreibt eine schon ausgefüllte `judge_meta_response.yaml`"* **komplett** — die transienten
  Round-Trip-Artefakte müssen gar nicht persistiert werden. Nur `judge_quality.md` (das echte Ergebnis)
  wird beim Ingest geschrieben.

## Nicht-Ziele (v1, YAGNI)

- **Keine** `/compare`-Integration — judge-meta ist **per-Bundle**; die Heimat ist die Ergebnisseite. Ein
  Cross-Judge-Aggregat (eine `/compare`-artige Judge-Qualitäts-Tabelle über mehrere Judge-Modelle) ist
  **Sub-Projekt 2**.
- **Keine** K.-o.-Outlier-Betonung in `judge_quality.md` (Sub-Projekt 3).
- **Kein** Datei-Upload — Textarea-Paste gewählt.
- **Kein** MD→HTML-Renderer — die Inline-Summary rendert aus den berechneten Objekten, nicht aus dem
  Markdown.
- **Keine** Änderung an `render_judge_quality_md` oder der übrigen F-Logik.
- **Kein** Persistieren von Anfrage-MD / Antwort-Vorlage (Stream-only, s. o.).

## Reuse (zahlt sich erneut aus)

- `touchstone/gui/bundles.py` → `bundle_detail(run_dir) -> dict | None`: einzige Detail-Quelle
  (`pack`, `reports`, `master_rows`, …). Die **judged-Vorbedingung** ist `detail is None or not
  detail.get("reports")` — identisch zum CLI (`cli.py:857,897`).
- `touchstone/gui/judge_meta.py` → die komplette F-Pipeline, unverändert aufgerufen.
- E-Muster (`app.py`): `Response(body, media_type=…, headers={"Content-Disposition": …})` für In-Memory
  MD/YAML-Downloads; Run-Dir-Confinement (`rd.resolve()`; `is_relative_to(runs_dir.resolve())` **vor**
  Disk-Zugriff).
- Pack-Editor-Muster (`app.py:149–191`, `pack_editor.js`): Textarea → `Form(...)` POST → Server-Validierung
  → JSON-Antwort `{ok, errors, …}` (**never-500**, ein Parse-/Schema-Fehler ist ein normales
  `{ok:false}`-Ergebnis, kein HTTP-500); Size-Cap (`_MAX_PACK_YAML`-Analog).
- Bestehende Allowlist-Route `GET /export/{name}/{fname}` (`app.py:445`, `allowed` enthält
  `judge_quality.md` bereits bei `app.py:454`) liefert die erzeugte Datei — **kein neuer Download-Pfad
  für das Ergebnis nötig**.

## Design

### Heimat — neue Karte auf der Ergebnisseite

`templates/result.html`, direkt **vor** der bestehenden Export-Karte (~Z. 476), neue Karte
**„Judge-Qualität (Meta-Eval)"**, gegated mit `{% if detail.reports %}` (nur bei bewertetem Bundle — wie
die „refuse unjudged"-Vorbedingung der CLI). Die Route `result()` (`app.py:319–368`) übergibt `detail` und
`run_dir` bereits — **keine neuen Kontextvariablen nötig**.

Karten-Aufbau:
- **Zeile 1 — „Export für die Cloud-KI"**: zwei Buttons (Anfrage `.md` + Antwort-Vorlage `.yaml`) +
  Kurzhinweis: „Teil A zuerst blind ausfüllen, dann Teil B".
- **Zeile 2 — „Antwort einlesen"**: Textarea (`x-model`) + Button „Auswerten" → POST (s. u.). Darunter
  Alpine-gesteuert **entweder** die Fehlerliste **oder** die Inline-Summary.
- **Wenn `judge_quality.md` schon existiert** (`{% if (run_dir / 'judge_quality.md').exists() %}`):
  Download-Button + Hinweis „bereits ausgewertet — Neu-Einlesen überschreibt". (`judge_quality.md` ist ein
  *abgeleitetes* Artefakt → Überschreiben ist unkritisch, anders als die response.yaml-Falle.)

### Routen (alle in `create_app()`, lazy-import `from touchstone.gui import judge_meta`)

```
GET  /export-judge-meta-request/{name}    → render_request_md(detail)
                                             Response text/markdown; attachment "judge_meta_request.md"
GET  /export-judge-meta-template/{name}    → empty_response_template(detail["pack"], cells)
                                             Response application/x-yaml; attachment "judge_meta_response.yaml"
POST /judge-meta-ingest/{name}  (yaml_text: str = Form(...))
                                             → {ok:false, errors:[…]}  |  {ok:true, summary_html, download_url}
```

- `cells = sorted({(r.model, r.variant) for r in detail["reports"]})` — exakt wie `cli.py:864–868`.
- **Beide GET-Routen:** Confinement → `rd = (runs_dir/name).resolve()`; `if not
  rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir(): 404`. Dann `detail = bundle_detail(rd)`;
  `if detail is None or not detail.get("reports"): 400` mit Hinweis „erst `touchstone judge` laufen lassen".
- **POST `/judge-meta-ingest/{name}`** (Reihenfolge):
  1. Confinement (404) · `len(yaml_text) > _MAX_META_YAML` → 400.
  2. `detail = bundle_detail(rd)`; unjudged → 400.
  3. `try: meta = judge_meta.parse_meta_response(yaml_text) except Exception as exc: return {ok:false,
     errors:[str(exc)]}` (HTTP 200, never-500 — Pack-Editor-Muster).
  4. `agreement = compute_agreement(detail["pack"], detail["reports"], detail["master_rows"], meta)` ·
     `rubric = aggregate_rubric(detail["reports"], meta)`.
  5. `md = render_judge_quality_md(detail, agreement, rubric, meta)` · `(rd/"judge_quality.md").write_text(
     md, encoding="utf-8")`.
  6. `summary_html = _templates.get_template("macros/_judge_meta_summary.html").render(agreement=agreement,
     rubric=rubric)` → `return {ok:true, summary_html, download_url: f"/export/{name}/judge_quality.md"}`.

### Frontend

- **Neues Partial `templates/macros/_judge_meta_summary.html`**: rendert **rein aus** `AgreementResult` +
  `RubricSummary` — mittlere |Δ| (Bundle), N Outlier 🚩, je Zelle Quality% lokal/cloud/Δ, die vier
  Rubrik-Pass-Raten. `None`-Werte als „—" (`compute_agreement` liefert `None` bei fehlenden Scores).
  **Kein** Zugriff auf F's Headline-String (das hieße `render_judge_quality_md` aufmachen → vermieden); die
  Karte zeigt die Zahlen, der volle Headline-Text steht im Download.
- **Neues `static/judge_meta.js`** (Alpine, analog `pack_editor.js`): `fetch` POST mit
  `FormData{yaml_text}`, JSON konsumieren; bei `{ok:false}` Fehlerliste zeigen, bei `{ok:true}`
  `summary_html` einsetzen + Download-Button auf `download_url` aktivieren.

## Datenfluss

```
Bundle (judged) ─ bundle_detail ─┬─[Export GET] render_request_md ─────────► judge_meta_request.md (Stream)
                                 └─[Export GET] empty_response_template ───► judge_meta_response.yaml (Stream)
   Mensch → Cloud-KI füllt YAML
   ─[Ingest POST, paste] parse_meta_response → compute_agreement + aggregate_rubric
        → render_judge_quality_md → schreibe judge_quality.md
        → Inline-Summary (Partial) + download_url=/export/{name}/judge_quality.md
```

## Fehlerbehandlung (alle never-500)

- **Unjudged Bundle** (kein/leeres `reports.jsonl`) → 400 mit „erst `touchstone judge`" (GET-Exports **und**
  POST-Ingest).
- **Path-Traversal / Symlink-Escape** → 404 (`resolve()` + `is_relative_to` **vor** Disk-Zugriff; Tests
  decken `name=../…` und symlinked-run-dir ab).
- **Malformed YAML** → `{ok:false, errors:[…]}` mit HTTP 200, kein Stacktrace. Deckt die drei CLI-Klassen:
  Non-Mapping → `ValueError`, pydantic `ValidationError`, leere/None-Eingabe.
- **Oversize `yaml_text`** → 400.
- **CSRF Origin-Guard** (`app.py:76–88`) greift automatisch für den POST; same-origin Alpine-`fetch` sendet
  keinen fremden Origin (wie `/import-bundle`).

## Tests (TDD, RED→GREEN) — `tests/test_gui_judge_meta_surface.py`

Muster aus `test_gui_pack_editor.py` (`_client(tmp_path)`-Helfer, `TestClient`) + `test_gui_meta_report_
routes.py` (Traversal/Confinement) + judged-Bundle-Fixture aus `test_cli_judge_meta.py` /
`test_gui_judge_quality_export.py`.

- **`/export-judge-meta-request/{name}`**: judged → 200, `text/markdown`, Body enthält „Teil A"/„Teil B";
  unjudged → 400; Traversal (`name=../…`) → 404.
- **`/export-judge-meta-template/{name}`**: judged → 200, valides YAML, enthält **jede** (model,variant)-Zelle
  × Pack-Dimension; round-trips durch `parse_meta_response`.
- **`/judge-meta-ingest/{name}`**: valides YAML → 200 `{ok:true}`, `judge_quality.md` auf Disk geschrieben,
  Antwort trägt `summary_html` + `download_url`; **malformed YAML** → 200 `{ok:false, errors}` (**kein
  500**); fehlende Pflichtfelder (pydantic) → `{ok:false}`; unjudged → 400; oversize → 400; Traversal → 404.
- **`result.html`**: Karte erscheint **nur** bei `detail["reports"]` (Kontext-/Template-Test, ggf. via
  gepatchtem `render`-Helfer wie in `test_gui_pack_editor`); zeigt Download-Button, wenn `judge_quality.md`
  schon existiert.
- **Inline-Summary-Partial**: `_judge_meta_summary.html` rendert mittlere |Δ|, Outlier-Zähler, Quality%-Δ je
  Zelle, vier Rubrik-Raten; `None`-Felder als „—".
- **Gate** (Lesson 4): voller `pytest -q` + `mypy touchstone/` (strict) + `ruff check . && ruff format
  --check .`.
- **Headless-Smoke** (Memory `gui-restart-after-changes`): GUI neu starten → echtes judged Bundle →
  Export-Buttons liefern MD/YAML → Vorlage von Hand füllen → Paste+Auswerten → Inline-Summary erscheint +
  `judge_quality.md` herunterladbar.
- **Adversariale Whole-Branch-Review** vor Merge (3 Linsen, Controller verifiziert jeden Fund selbst):
  (1) Confinement/Traversal an **jeder** der drei Routen, (2) never-500 bei malformed YAML, (3) der
  judged-Gate greift überall (kein Render auf unjudged Bundle).

## Implementierungs-Reihenfolge (Plan)

1. `GET /export-judge-meta-request/{name}` + `GET /export-judge-meta-template/{name}` (Confinement +
   judged-Gate, Stream-only) + Route-Tests.
2. `POST /judge-meta-ingest/{name}` (parse → compute → render → write → JSON) + `_MAX_META_YAML` +
   never-500-Pfad + Route-Tests.
3. Partial `macros/_judge_meta_summary.html` (rein aus `AgreementResult`/`RubricSummary`) + Render-Test.
4. `static/judge_meta.js` (Alpine) + Karte in `result.html` (gegated) + Template-Test.
5. Volle Suite + `mypy` + `ruff` (check & format) + Headless-Smoke (GUI neu starten).
6. Adversariale Review → bestätigte Funde fixen → Merge nach `main` + Push (kein PR, Solo-Repo).

## Berührte Dateien

- `touchstone/gui/app.py` — drei neue Routen in `create_app()`, `_MAX_META_YAML`-Konstante.
- `touchstone/gui/templates/result.html` — neue Karte vor der Export-Karte.
- `touchstone/gui/templates/macros/_judge_meta_summary.html` (neu).
- `touchstone/gui/static/judge_meta.js` (neu).
- `touchstone/gui/judge_meta.py`, `touchstone/gui/bundles.py`, `touchstone/gui/report_md.py` —
  **unverändert** genutzt.
- `tests/test_gui_judge_meta_surface.py` (neu).

## Risiken / Edge-Cases

- **Confinement-Lücke an einer der drei Routen** = der kanonische Bug hier → jede Route resolved +
  `is_relative_to` vor Disk-Zugriff; Tests erzwingen es an allen dreien.
- **500 statt `{ok:false}` bei kaputtem YAML** → breites `try/except` um `parse_meta_response`, expliziter
  Test mit fehlenden Pflichtfeldern.
- **Karte erscheint auf unjudged Bundle** → `{% if detail.reports %}`-Gate + Route-seitiger 400; beide
  getestet.
- **Allowlist-Drift** — `judge_quality.md` ist bereits in der Einzelfile-Allowlist (`app.py:454`) **und** im
  Bundle-Ledger (`app.py:652`); dieses Sub-Projekt fügt **kein** neues Artefakt hinzu (Stream-only), also
  keine Allowlist-Pflege nötig. (Falls das später anders wird: beide Listen synchron halten.)
- **`detail["master_rows"]` fehlt/leer** (K.-o.-Konkordanz-Quelle) → `compute_agreement` behandelt fehlende
  Scores als `None`; das Partial zeigt „—". Kein Hard-Fail.
