# B1 — Verschmolzene Single-Run-Ergebnisseite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/result/{name}` und `/compare/{name}` zu einer Single-Run-Ergebnisseite verschmelzen (eine Scroll-Seite, Vergleich zuerst); `compare_axis.html` entfällt.

**Architecture:** Die Route `/result/{name}` lädt das Bundle **einmal** (`bundle_detail`) und reicht es als `base` in `compare.compare_detail` (heute lädt das intern erneut → Doppelladung beseitigt). `result.html` rendert beide Blöcke: oben den Achsen-Vergleich (Scatter / Kopf-an-Kopf / Per-Aufgabe-Δ), unten Detail (Antworten / Perf / Export). `/compare/{name}` wird ein 301-Redirect. Adaptive Tabellen: Kopf-an-Kopf immer (≥2 Zellen), flache Master-Scorecard zusätzlich nur bei echter Matrix.

**Tech Stack:** Python 3.12 · FastAPI + Jinja2 (`[gui]` extra) · Alpine.js (client-seitiger Zell-Filter) · pytest · ruff · mypy strict.

## Global Constraints

- Python 3.12, `uv` only. Ruff (line-length 100) + mypy strict müssen nach **jedem** Task grün sein (`uv run ruff check . && uv run ruff format . && uv run mypy touchstone/`).
- Deutsch in UI-Text/Kommentaren ok; Code/Identifier englisch.
- **`/compare` (ohne Name)** — Cross-Run-Aggregat, `compare.html` — bleibt **unangetastet** (das ist B2).
- Pure Logik (`compare.py`) bleibt unit-getestet; Routen über den FastAPI-`TestClient`.
- Kanonische Datenbasis bleibt `result.json` / Bundle-jsonl (Projekt A) — keine neuen Metriken.
- Keine in-process-Messung; GUI bleibt out-of-process control-plane.

---

### Task 1: `compare_detail` akzeptiert vorgeladene `base` (Doppelladung beseitigen)

**Files:**
- Modify: `touchstone/gui/compare.py:190-213` (`compare_detail`)
- Test: `tests/test_gui_compare.py`

**Interfaces:**
- Consumes: `bundles.bundle_detail(run_dir) -> dict | None` (keys: `responses`, `pack`, `reports`, `master_rows`, `verdicts`, `ram`, `cpu`, …).
- Produces: `compare_detail(run_dir, axis=None, *, projection=None, base=None) -> CompareDetail | None` — wenn `base` übergeben wird, ruft die Funktion `bundle_detail` **nicht** erneut auf.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_compare.py
from unittest.mock import patch
from pathlib import Path
from touchstone.gui import compare

def test_compare_detail_reuses_passed_base(tmp_path, monkeypatch):
    """When a base dict is passed, compare_detail must not re-call bundle_detail."""
    # Build a base dict via the real loader once, against a fixture bundle.
    from touchstone.gui import bundles
    rd = _fixture_bundle(tmp_path)  # helper already used in this test module
    base = bundles.bundle_detail(rd)
    assert base is not None
    with patch.object(bundles, "bundle_detail") as spy:
        detail = compare.compare_detail(rd, "variant", base=base)
    spy.assert_not_called()
    assert detail is not None
    assert detail.run_name == rd.name
```

(Verwende den vorhandenen Bundle-Fixture-Helper aus `tests/test_gui_compare.py`; falls keiner existiert, kopiere das Fixture-Setup aus `tests/test_gui_compare_route.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare.py::test_compare_detail_reuses_passed_base -v`
Expected: FAIL — `bundle_detail` wird aufgerufen (spy.assert_not_called erhebt AssertionError) oder TypeError (unbekanntes kwarg `base`).

- [ ] **Step 3: Implement**

In `compare_detail` die Signatur erweitern und den Lade-Aufruf gaten:

```python
def compare_detail(
    run_dir: Path,
    axis: str | None = None,
    *,
    projection: str | None = None,
    base: dict[str, Any] | None = None,
) -> CompareDetail | None:
    """Project a bundle along ``axis`` (model|variant), holding the other dimension
    constant. Returns None if the bundle has no loadable pack. ``base`` lets the caller
    pass an already-loaded ``bundle_detail`` dict to avoid a second load.
    """
    from touchstone.gui import bundles  # local import avoids a cycle

    base = base if base is not None else bundles.bundle_detail(run_dir)
    if base is None:
        return None
    # … rest unchanged …
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare.py -v`
Expected: PASS. Dann volle Suite + Lint: `uv run pytest -q && uv run ruff check . && uv run mypy touchstone/`

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/compare.py tests/test_gui_compare.py
git commit -m "refactor(gui): compare_detail accepts preloaded base (no double bundle load)"
```

---

### Task 2: Adaptive-Matrix-Flag in `axis_options`

**Files:**
- Modify: `touchstone/gui/compare.py:25-58` (`AxisOptions` + `axis_options`)
- Test: `tests/test_gui_compare.py`

**Interfaces:**
- Produces: `AxisOptions.full_matrix: bool` — `True` iff `len(models) > 1 and len(variants) > 1` (echte Matrix → flache Master-Scorecard zusätzlich nötig).

- [ ] **Step 1: Write the failing test**

```python
def test_axis_options_full_matrix_flag():
    from touchstone.results import EvalResponse
    def r(model, variant):
        return EvalResponse(model=model, variant=variant, prompt_id="p1", category="c",
                            repeat=0, response_text="x", ok=True)  # fill required fields per EvalResponse
    # 1×2 → not a full matrix
    o = compare.axis_options([r("m", "baseline"), r("m", "none")])
    assert o.full_matrix is False
    # 2×2 → full matrix
    o2 = compare.axis_options([r("m1","baseline"), r("m1","none"), r("m2","baseline"), r("m2","none")])
    assert o2.full_matrix is True
```

(Fülle die `EvalResponse`-Pflichtfelder gemäß `touchstone/results.py`; orientiere dich an bestehenden EvalResponse-Konstruktionen in `tests/test_gui_compare.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare.py::test_axis_options_full_matrix_flag -v`
Expected: FAIL — `AxisOptions` hat kein Feld `full_matrix`.

- [ ] **Step 3: Implement**

`AxisOptions` um `full_matrix: bool` erweitern; in `axis_options` setzen:

```python
@dataclass
class AxisOptions:
    models: list[str]
    variants: list[str]
    default_axis: str
    default_label: str
    comparable: bool
    full_matrix: bool  # >1 model AND >1 variant → needs the flat master-scorecard too

# in axis_options(...), before return:
    full_matrix = len(models) > 1 and len(variants) > 1
    return AxisOptions(models, variants, axis, label, comparable, full_matrix)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare.py -v && uv run mypy touchstone/`
Expected: PASS. (Falls `AxisOptions` woanders positional konstruiert wird, ergänze das Feld dort — grep `AxisOptions(`.)

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/compare.py tests/test_gui_compare.py
git commit -m "feat(gui): AxisOptions.full_matrix flag for adaptive scorecard"
```

---

### Task 3: `/result/{name}` lädt einmal + liefert Vergleichsdaten

**Files:**
- Modify: `touchstone/gui/app.py:163-186` (`result` route)
- Test: `tests/test_gui_app_detail.py`

**Interfaces:**
- Consumes: `bundles.bundle_detail`, `bundles.classify`, `compare.axis_options`, `compare.compare_detail(..., base=…)` (Task 1/2).
- Produces: `result.html`-Kontext erhält zusätzlich `compare_detail` (CompareDetail | None), `axis_opts` (AxisOptions | None). `compare_opts` bleibt für Rückwärtskompatibilität (= `axis_opts`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_app_detail.py — uses the existing TestClient fixture `client`
def test_result_includes_compare_block_for_multi_cell(client, multi_cell_bundle):
    """A ≥2-cell judged bundle renders the head-to-head comparison inside /result."""
    resp = client.get(f"/result/{multi_cell_bundle.name}")
    assert resp.status_code == 200
    body = resp.text
    assert "Kopf-an-Kopf" in body            # comparison table migrated in
    assert "Effizienz-Relation" in body      # scatter section migrated in
```

(`multi_cell_bundle` = ein judged Bundle mit ≥2 Zellen; nutze/erweitere den Fixture-Helper aus `tests/test_gui_compare_route.py`, der genau so ein Bundle baut.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_app_detail.py::test_result_includes_compare_block_for_multi_cell -v`
Expected: FAIL — `result.html` enthält die Vergleichs-Sektionen noch nicht (kommt in Task 4; dieser Step deckt die Route-Verdrahtung ab, die Assertion wird erst nach Task 4 grün — daher hier nur die Route-Daten testen, s. Hinweis).

> **Hinweis Reihenfolge:** Task 3 verdrahtet nur die Daten; die HTML-Strings erscheinen erst mit Task 4. Damit Task 3 eigenständig grün wird, prüfe in Step 1 stattdessen die **Daten**, nicht das HTML:

```python
def test_result_route_passes_compare_detail(client, multi_cell_bundle, monkeypatch):
    seen = {}
    import touchstone.gui.app as appmod
    real = appmod.render
    def spy_render(template, request, **ctx):
        seen.update(ctx); return real(template, request, **ctx)
    monkeypatch.setattr(appmod, "render", spy_render)
    resp = client.get(f"/result/{multi_cell_bundle.name}")
    assert resp.status_code == 200
    assert seen.get("compare_detail") is not None
    assert seen["compare_detail"].cells and len(seen["compare_detail"].cells) >= 2
```

- [ ] **Step 3: Implement**

```python
@app.get("/result/{name}", response_class=HTMLResponse)
def result(
    request: Request,
    name: str,
    axis: str | None = None,
    variant: str | None = None,
    model: str | None = None,
) -> HTMLResponse:
    rd = (runs_dir / name).resolve()
    if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
        raise HTTPException(status_code=404)
    if axis is not None and axis not in ("model", "variant"):
        raise HTTPException(status_code=422)
    try:
        detail = bundles.bundle_detail(rd)
        summary = bundles.classify(rd)
    except Exception:
        detail, summary = None, bundles.BundleSummary(run_dir=rd, status="error")
    axis_opts = compare.axis_options(detail["responses"]) if detail and detail.get("responses") else None
    projection = variant or model
    cmp = (
        compare.compare_detail(rd, axis, projection=projection, base=detail)
        if detail and axis_opts and axis_opts.comparable
        else None
    )
    return render(
        "result.html", request,
        detail=detail, summary=summary, run_dir=rd,
        compare_opts=axis_opts, compare_detail=cmp, axis_opts=axis_opts,
        active="overview",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_app_detail.py -v && uv run mypy touchstone/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/app.py tests/test_gui_app_detail.py
git commit -m "feat(gui): /result loads bundle once and passes axis comparison data"
```

---

### Task 4: `result.html` integriert den Vergleichs-Block; `compare_axis.html` entfällt

**Files:**
- Create: `touchstone/gui/templates/macros/_compare_block.html` (Scatter + Kopf-an-Kopf + Per-Aufgabe-Δ + Achsen-Umschalter)
- Modify: `touchstone/gui/templates/result.html` (Block einbinden, oberhalb der Antworten)
- Modify: `touchstone/gui/app.py:194-212` (`compare_axis` route — wird Task 5/Redirect; hier nur sicherstellen, dass nichts mehr `compare_axis.html` rendert)
- Delete: `touchstone/gui/templates/compare_axis.html`
- Test: `tests/test_gui_app_detail.py`

**Interfaces:**
- Consumes: `compare_detail` (CompareDetail) aus dem Route-Kontext (Task 3). Links zeigen auf `/result/{{ run_dir.name }}?axis=…` (nicht mehr `/compare/…`).

- [ ] **Step 1: Write the failing test** (das HTML-Assertion-Pendant zu Task 3)

```python
def test_result_renders_headtohead_and_scatter(client, multi_cell_bundle):
    body = client.get(f"/result/{multi_cell_bundle.name}").text
    assert "Kopf-an-Kopf" in body
    assert "Effizienz-Relation" in body
    assert 'href="/result/' in body and "?axis=" in body   # axis switch points at /result
    assert "/compare/" not in body                          # no dead within-bundle compare links
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_app_detail.py::test_result_renders_headtohead_and_scatter -v`
Expected: FAIL — Sektionen fehlen in `result.html`.

- [ ] **Step 3: Implement**

1. Lege `templates/macros/_compare_block.html` an: migriere die Sektionen ①/②/③ + den Achsen-/Projektions-Umschalter aus `compare_axis.html` (Zeilen 17–126). Ersetze in ALLEN Links `/compare/{{ detail.run_name }}` → `/result/{{ detail.run_name }}` und referenziere die übergebene Variable `compare_detail` statt `detail` (oder rendere via `{% with detail=compare_detail %}`). Behalte `<script defer src="/static/scatter.js">` und `{% include "_method_explainer.html" %}`.
2. In `result.html` direkt nach dem Kopf/Status-Block und vor „Antworten nach Kategorie" einbinden:

```jinja
{% if compare_detail and not compare_detail.single %}
  {% with detail=compare_detail %}{% include "macros/_compare_block.html" %}{% endwith %}
{% endif %}
```

3. Entferne in `result.html` den alten `↔ Vergleichen`-Button (Zeile ~81) — der Vergleich ist jetzt inline.
4. Lösche `templates/compare_axis.html`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_app_detail.py tests/test_gui_compare_route.py -v`
Expected: PASS (compare_route-Tests ggf. in Task 5 angepasst). Falls ein Test noch `compare_axis.html` erwartet, in Task 5 mit umgestellt.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/macros/_compare_block.html touchstone/gui/templates/result.html
git rm touchstone/gui/templates/compare_axis.html
git commit -m "feat(gui): inline axis comparison into result page; drop compare_axis.html"
```

---

### Task 5: `/compare/{name}` → 301-Redirect auf `/result`

**Files:**
- Modify: `touchstone/gui/app.py:194-212` (`compare_axis` route → Redirect)
- Test: `tests/test_gui_compare_route.py`

**Interfaces:**
- Produces: `GET /compare/{name}?axis=&variant=&model=` → `RedirectResponse(/result/{name}?<same querystring>, status_code=301)`.

- [ ] **Step 1: Write the failing test**

```python
def test_compare_name_redirects_to_result(client, multi_cell_bundle):
    resp = client.get(f"/compare/{multi_cell_bundle.name}?axis=variant", follow_redirects=False)
    assert resp.status_code == 301
    loc = resp.headers["location"]
    assert loc.startswith(f"/result/{multi_cell_bundle.name}")
    assert "axis=variant" in loc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare_route.py::test_compare_name_redirects_to_result -v`
Expected: FAIL — Route rendert noch `compare_axis.html` (200, kein 301).

- [ ] **Step 3: Implement**

```python
from fastapi.responses import RedirectResponse

@app.get("/compare/{name}")
def compare_axis(request: Request, name: str) -> RedirectResponse:
    qs = request.url.query
    target = f"/result/{name}" + (f"?{qs}" if qs else "")
    return RedirectResponse(target, status_code=301)
```

Entferne den nun toten Import/Code, der `compare.compare_detail` direkt für die alte Route nutzte (Route lädt nichts mehr selbst). `compare.compare_detail` bleibt — es wird von `/result` genutzt.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare_route.py -v`
Expected: PASS. Passe bestehende compare_route-Tests an, die ein 200 + `compare_axis.html`-Inhalt erwarteten → jetzt 301 oder Inhalt über `/result` prüfen. (Tote Assertions migrieren, nicht löschen.)

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/app.py tests/test_gui_compare_route.py
git commit -m "feat(gui): /compare/{name} 301-redirects to merged /result page"
```

---

### Task 6: Adaptive flache Master-Scorecard nur bei echter Matrix

**Files:**
- Modify: `touchstone/gui/templates/result.html` (Master-Scorecard-Sektion ~Zeile 154)
- Test: `tests/test_gui_app_detail.py`

**Interfaces:**
- Consumes: `axis_opts.full_matrix` (Task 2) im Kontext. Die bestehende Master-Scorecard (Zeilen=alle Zellen) wird nur gerendert, wenn `not compare_detail` (1 Zelle / nicht vergleichbar) **oder** `axis_opts.full_matrix` (echte Matrix). Bei 1×N / N×1 ersetzt die Kopf-an-Kopf-Tabelle sie.

- [ ] **Step 1: Write the failing test**

```python
def test_flat_scorecard_only_for_full_matrix(client, two_by_two_bundle, one_by_n_bundle):
    # 1×N: head-to-head present, flat master-scorecard suppressed
    b1 = client.get(f"/result/{one_by_n_bundle.name}").text
    assert "Kopf-an-Kopf" in b1
    assert "Gewichtete Master-Scorecard" not in b1
    # 2×2: both present
    b2 = client.get(f"/result/{two_by_two_bundle.name}").text
    assert "Kopf-an-Kopf" in b2
    assert "Gewichtete Master-Scorecard" in b2
```

(Fixtures: `one_by_n_bundle` = 1 Modell × ≥2 Varianten; `two_by_two_bundle` = 2×2. Baue sie über den vorhandenen Bundle-Fixture-Helper.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_app_detail.py::test_flat_scorecard_only_for_full_matrix -v`
Expected: FAIL — Master-Scorecard wird immer gerendert.

- [ ] **Step 3: Implement**

Umschließe die Master-Scorecard-Card in `result.html`:

```jinja
{% if (not compare_detail or compare_detail.single) or (axis_opts and axis_opts.full_matrix) %}
  {# … bestehende „Gewichtete Master-Scorecard"-Card unverändert … #}
{% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_app_detail.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/result.html tests/test_gui_app_detail.py
git commit -m "feat(gui): show flat master-scorecard only for true N×M matrices"
```

---

### Task 7: Antworten-Zell-Filter (client-seitig) + Default = beste Zelle

**Files:**
- Modify: `touchstone/gui/templates/result.html` („Antworten nach Kategorie"-Sektion ~Zeile 246)
- Test: `tests/test_gui_app_detail.py`

**Interfaces:**
- Verhalten: Bei ≥2 Zellen erhält die Antworten-Sektion ein Alpine-`x-data` mit `cell`-Filter (Buttons je Zell-Label + „alle"). Default-Zelle = die mit höchster `pct` (Gleichstand → höchstes `rubric_level`, dann erste). Alle Antworten bleiben im DOM (`x-show`, nicht serverseitig gefiltert → durchsuchbar/druckbar).

- [ ] **Step 1: Write the failing test**

```python
def test_answers_have_cell_filter_for_multi_cell(client, multi_cell_bundle):
    body = client.get(f"/result/{multi_cell_bundle.name}").text
    assert 'x-data' in body and 'cell' in body          # Alpine filter state present
    assert 'data-cell-filter' in body                   # filter control marker
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_app_detail.py::test_answers_have_cell_filter_for_multi_cell -v`
Expected: FAIL — kein Filter-Markup.

- [ ] **Step 3: Implement**

In `result.html`, „Antworten nach Kategorie"-Card: bei `compare_detail and not compare_detail.single` einen Alpine-Wrapper. Default aus dem Kontext berechnen — ergänze in Task 3's Route einen `default_cell`-Wert ODER inline im Template via `compare_detail.winners.pct` (das Label der Qualitäts-Gewinnerzelle; Fallback erste Zelle). Markup-Skizze:

```jinja
{% set default_cell = compare_detail.winners.pct if compare_detail and compare_detail.winners.pct else (compare_detail.cells[0].label if compare_detail and compare_detail.cells else "") %}
<div x-data="{ cell: '{{ default_cell }}' }">
  <div data-cell-filter class="flex gap-2 text-xs" {% if not (compare_detail and not compare_detail.single) %}style="display:none"{% endif %}>
    <button @click="cell='__all__'">alle</button>
    {% for c in (compare_detail.cells if compare_detail else []) %}
      <button @click="cell='{{ c.label }}'">{{ c.label }}</button>
    {% endfor %}
  </div>
  {# je Antwort-Block ein Attribut data-cell="<label>" + x-show="cell==='__all__' || cell==='<label>'" #}
</div>
```

Hänge an jeden Antwort-Block (pro Modell/Variante) `:data-cell` + `x-show="cell==='__all__' || cell==='<label>'"`. Bei genau 1 Zelle bleibt der Filter unsichtbar (`display:none`) und `cell` zeigt diese Zelle.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_app_detail.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/result.html tests/test_gui_app_detail.py
git commit -m "feat(gui): client-side cell filter for the answers section (default: best cell)"
```

---

### Task 8: Tote Links umbiegen + Übersichts-Button + volle Verifikation

**Files:**
- Modify: `touchstone/gui/templates/overview.html:121` (`↔ Vergleichen` → `/result/{name}?axis=…`)
- Modify: ggf. `touchstone/gui/app.py:77` (`compare_links` bleibt — Ziel-URL ist jetzt `/result`)
- Test: `tests/test_gui_overview_live.py`, volle Suite

**Interfaces:**
- Der Overview-„↔ Vergleichen"-Button zeigt auf `/result/{{ b.run_dir.name }}?axis={{ co.default_axis }}`.

- [ ] **Step 1: Write the failing test**

```python
def test_overview_compare_button_points_at_result(client, multi_cell_bundle):
    body = client.get("/").text
    assert f"/result/{multi_cell_bundle.name}?axis=" in body
    assert f"/compare/{multi_cell_bundle.name}" not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_overview_live.py::test_overview_compare_button_points_at_result -v`
Expected: FAIL — Button zeigt noch auf `/compare/{name}`.

- [ ] **Step 3: Implement**

In `overview.html:121` `href="/compare/{{ b.run_dir.name }}?axis={{ co.default_axis }}"` → `href="/result/{{ b.run_dir.name }}?axis={{ co.default_axis }}"`. `app.py:77` (`compare.axis_options_for_dir`) bleibt; nur das Link-Ziel ändert sich.

- [ ] **Step 4: Run full verification**

Run:
```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy touchstone/
grep -rn "/compare/" touchstone/gui/templates/   # expect: no within-bundle compare links remain
grep -rn "compare_axis" touchstone/               # expect: no references to the deleted template
```
Expected: alle grün; beide greps leer (außer evtl. der `/compare`-Cross-Run-Link ohne Name, der bleibt).

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/overview.html tests/test_gui_overview_live.py
git commit -m "chore(gui): point overview compare button at merged /result; drop dead compare links"
```

---

## Notes für die Umsetzung

- **Fixtures:** `tests/test_gui_compare_route.py` enthält bereits Helfer, die judged Multi-Zell-Bundles auf Platte bauen. Wiederverwenden/teilen statt neu erfinden (ggf. nach `tests/conftest.py` heben, wenn mehrere Test-Dateien sie brauchen).
- **Reihenfolge:** Task 3 (Daten) vor Task 4 (HTML) — Task 3's Step-1-Test prüft den Route-Kontext, Task 4's den gerenderten HTML-String.
- **`compare.html` (Cross-Run, B2) niemals anfassen.**
- Nach jedem Task: `uv run pytest -q && uv run ruff check . && uv run mypy touchstone/` grün, bevor weiter.
