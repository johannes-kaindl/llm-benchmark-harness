# B2 — Cross-Run-Vergleichsseite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/compare` wird eine Auswahl-+-Vergleichsseite: einzelne Bundle-Zeilen (HW×Modell×Quant×Variante) auswählen/importieren, dann ein Auto-Diff-Vergleich (GEMEINSAM-Block + variierende Spalten + Winner).

**Architecture:** Zwei neue pure Funktionen in `aggregate.py` — `pool_rows(runs_dir)` (pro-Bundle-Zeilen mit `run_dir`-Herkunft + stabiler ID) und `diff_rows(selected)` (common/varying-Erkennung + Winner) — speisen die `/compare`-Route (`?rows=<id>,…`). `compare.html` wird neu: Auswahl-Pool oben (Tabelle + Alpine-Filter + Import + „Vergleichen"), Auto-Diff-Partial unten. Die bestehende `aggregate()` (CLI-Pfad) bleibt unberührt.

**Tech Stack:** Python 3.12 · FastAPI + Jinja2 (`[gui]` extra) · Alpine.js (Filter + Auswahl + Import-fetch) · stdlib csv · pytest · ruff · mypy strict.

## Global Constraints

- Python 3.12, `uv` only. Ruff (line-length 100) + mypy strict müssen nach **jedem** Task grün sein (`uv run ruff check . && uv run ruff format . && uv run mypy touchstone/`).
- Deutsch in UI-Text/Kommentaren ok; Code/Identifier englisch.
- **Die bestehende `aggregate.aggregate()` (über GroupKey gemittelt) bleibt unverändert** — sie speist den CLI-Pfad `touchstone aggregate`. B2 **fügt** Funktionen hinzu.
- `/compare/{name}` (der B1-Redirect) und `compare.html`'s B1-Nachbarn (`result.html`, `_compare_block.html`) **nicht** anfassen.
- **Scope-Disziplin:** Nur die je Task genannten Dateien. Format nur geänderte Python-Dateien (`uv run ruff format <files>`), **nie** `ruff format .` tree-weit. `git add` explizit, nie `git add -A`. `git status --short` vor jedem Commit prüfen — nur beabsichtigte Dateien.
- Identifizierende Dimensionen (für common/varying): `chip, ram_gb, pack, pack_version, model, quant, variant` — **kein** `machine` (Projekt-A-Provenance-Hygiene).
- Robuste Routen: ungültige/unbekannte `rows`-IDs werden ignoriert, **nie 500**.

---

### Task 1: `pool_rows` — pro-Bundle-Zeilen mit stabiler ID

**Files:**
- Modify: `touchstone/aggregate.py` (Helper extrahieren + `pool_rows` + `PoolRow` hinzufügen)
- Test: `tests/test_aggregate.py` (oder neue `tests/test_pool_rows.py` falls test_aggregate.py groß)

**Interfaces:**
- Consumes: `load_scores_csv(path) -> list[dict[str,str]]` (existiert).
- Produces:
  - `@dataclass PoolRow` mit Feldern: `id: str` (`f"{run_name}|{model}|{variant}"`), `run_name: str`, `chip: str`, `ram_gb: str`, `pack: str`, `pack_version: str`, `model: str`, `quant: str`, `variant: str`, `quality_pct: float | None`, `ttft_p50: str`, `decode_med: str`, `peak_ram_gb: str`, `model_delta_gb: str`, `power: str`.
  - `pool_rows(runs_dir: str | Path) -> list[PoolRow]` — eine Zeile pro `(run_name, model, variant)` über alle `runs_dir/*/scores.csv`, `run_name = scores.csv.parent.name`.
  - `_weighted_quality(rows: list[dict[str,str]]) -> tuple[float | None, dict[str,int]]` — extrahiert aus `aggregate()` (Σ score·weight / Σ 5·weight über `metric_type=='dimension'`-Zeilen), von `aggregate()` UND `pool_rows` genutzt (DRY).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_aggregate.py
from pathlib import Path
from touchstone import aggregate

def _write_scores(d: Path, rows: list[dict]):
    import csv
    d.mkdir(parents=True, exist_ok=True)
    cols = ["chip","ram_gb","pack","pack_version","model","quant","variant",
            "ttft_p50","decode_med","peak_ram_gb","model_delta_gb","power",
            "metric_type","metric","weight","score"]
    with (d / "scores.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows: w.writerow(r)

def test_pool_rows_one_row_per_bundle_model_variant(tmp_path):
    # two runs, same setup → two distinct pool rows (NOT averaged), unique ids
    base = {"chip":"M1","ram_gb":"16","pack":"ndassist","pack_version":"1",
            "model":"gemma","quant":"Q4","variant":"baseline",
            "ttft_p50":"0.1","decode_med":"18","peak_ram_gb":"14","model_delta_gb":"8","power":"ac"}
    dim = {**base, "metric_type":"dimension","metric":"D1","weight":"1","score":"4"}
    _write_scores(tmp_path / "2026-01-01_000000_eval_ndassist", [dim])
    _write_scores(tmp_path / "2026-01-02_000000_eval_ndassist", [dim])
    pool = aggregate.pool_rows(tmp_path)
    assert len(pool) == 2
    ids = {r.id for r in pool}
    assert ids == {"2026-01-01_000000_eval_ndassist|gemma|baseline",
                   "2026-01-02_000000_eval_ndassist|gemma|baseline"}
    r0 = pool[0]
    assert r0.chip == "M1" and r0.model == "gemma" and r0.variant == "baseline"
    assert r0.quality_pct == 80.0   # score 4 of 5 = 80%
    assert r0.decode_med == "18"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_aggregate.py::test_pool_rows_one_row_per_bundle_model_variant -v`
Expected: FAIL — `aggregate` has no attribute `pool_rows`.

- [ ] **Step 3: Implement**

In `aggregate.py`: extrahiere die Quality-Schleife aus `aggregate()` in `_weighted_quality`, lass `aggregate()` sie aufrufen (gleiches Ergebnis), und füge `PoolRow` + `pool_rows` hinzu:

```python
def _weighted_quality(rows: list[dict[str, str]]) -> tuple[float | None, dict[str, int]]:
    dim_scores: dict[str, int] = {}
    wsum = wmax = 0
    for r in rows:
        if r.get("metric_type") != "dimension":
            continue
        score = _as_int(r.get("score", ""))
        if score is None:
            continue
        weight = _as_int(r.get("weight", "")) or 0
        dim_scores[r.get("metric", "")] = score
        wsum += score * weight
        wmax += SCALE_MAX * weight
    return ((wsum / wmax * 100.0) if wmax else None), dim_scores


@dataclass
class PoolRow:
    id: str
    run_name: str
    chip: str
    ram_gb: str
    pack: str
    pack_version: str
    model: str
    quant: str
    variant: str
    quality_pct: float | None
    ttft_p50: str
    decode_med: str
    peak_ram_gb: str
    model_delta_gb: str
    power: str


def pool_rows(runs_dir: str | Path) -> list[PoolRow]:
    """One row per (run_name, model, variant) — NOT averaged across bundles (unlike
    aggregate()), so two runs of the same setup stay distinct. id = run_name|model|variant."""
    base = Path(runs_dir)
    if not base.exists():
        return []
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    order: list[tuple[str, str, str]] = []
    for f in sorted(base.rglob("scores.csv")):
        run_name = f.parent.name
        for r in load_scores_csv(f):
            k = (run_name, r.get("model", ""), r.get("variant", ""))
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(r)
    out: list[PoolRow] = []
    for (run_name, model, variant) in order:
        grp = groups[(run_name, model, variant)]
        first = grp[0]
        quality, _dims = _weighted_quality(grp)
        out.append(
            PoolRow(
                id=f"{run_name}|{model}|{variant}",
                run_name=run_name,
                chip=first.get("chip", ""),
                ram_gb=first.get("ram_gb", ""),
                pack=first.get("pack", ""),
                pack_version=first.get("pack_version", ""),
                model=model,
                quant=first.get("quant", ""),
                variant=variant,
                quality_pct=quality,
                ttft_p50=first.get("ttft_p50", ""),
                decode_med=first.get("decode_med", ""),
                peak_ram_gb=first.get("peak_ram_gb", ""),
                model_delta_gb=first.get("model_delta_gb", ""),
                power=first.get("power", ""),
            )
        )
    return out
```
Refactor `aggregate()`'s inner quality loop to call `_weighted_quality(grp)` (keep `n_dims=len(dim_scores)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_aggregate.py -v && uv run mypy touchstone/`
Expected: PASS — including the **existing** aggregate tests (refactor must not change their results).

- [ ] **Step 5: Commit**

```bash
git add touchstone/aggregate.py tests/test_aggregate.py
git commit -m "feat(aggregate): pool_rows — per-bundle rows with stable id (DRY _weighted_quality)"
```

---

### Task 2: `diff_rows` — common/varying + winners

**Files:**
- Modify: `touchstone/aggregate.py` (add `CompareColumn`, `CompareDiff`, `diff_rows`)
- Test: `tests/test_aggregate.py`

**Interfaces:**
- Consumes: `PoolRow` (Task 1).
- Produces:
  - `@dataclass CompareColumn` mit `id: str`, `header: str`, `row: PoolRow`.
  - `@dataclass CompareDiff` mit `common: list[tuple[str, str]]` (`(dim_label, value)` für über alle konstante Dims), `varying: list[str]` (Dim-Namen die differieren; leer → der Lauf ist die Achse), `columns: list[CompareColumn]`, `winners: dict[str, str | None]` (metric → gewinnende column-`id`, None bei Gleichstand/fehlend).
  - `diff_rows(selected: list[PoolRow]) -> CompareDiff`.
- Konstanten: `DIM_LABELS = [("chip","Chip"),("ram_gb","RAM"),("pack","Pack"),("pack_version","Pack-Version"),("model","Modell"),("quant","Quant"),("variant","Variante")]`.

- [ ] **Step 1: Write the failing test**

```python
def test_diff_rows_cross_machine(tmp_path):
    from touchstone.aggregate import PoolRow, diff_rows
    def pr(rid, chip, ram, decode, ram_gb_peak):
        return PoolRow(id=rid, run_name=rid, chip=chip, ram_gb=ram, pack="ndassist",
                       pack_version="1", model="gemma", quant="Q4", variant="baseline",
                       quality_pct=76.0, ttft_p50="0.1", decode_med=decode,
                       peak_ram_gb=ram_gb_peak, model_delta_gb="8", power="ac")
    d = diff_rows([pr("a","M1","16","18","14"), pr("b","M5","64","42","18")])
    # model/pack/quant/variant constant → common; chip/ram_gb vary → headers
    common_dims = {dim for dim, _ in d.common}
    assert "model" in common_dims and "pack" in common_dims and "quant" in common_dims
    assert set(d.varying) == {"chip", "ram_gb"}
    assert d.columns[0].header and d.columns[1].header  # non-empty headers from varying values
    assert d.winners["decode_med"] == "b"        # 42 > 18, higher is better
    assert d.winners["quality_pct"] is None       # tie (76 == 76)

def test_diff_rows_same_setup_axis_is_run(tmp_path):
    from touchstone.aggregate import PoolRow, diff_rows
    def pr(rid, decode):
        return PoolRow(id=rid, run_name=rid, chip="M1", ram_gb="16", pack="ndassist",
                       pack_version="1", model="gemma", quant="Q4", variant="baseline",
                       quality_pct=76.0, ttft_p50="0.1", decode_med=decode,
                       peak_ram_gb="14", model_delta_gb="8", power="ac")
    d = diff_rows([pr("run-A","18"), pr("run-B","42")])
    assert d.varying == []                         # all dims identical
    assert d.columns[0].header == "run-A"          # header falls back to run_name
    assert d.winners["decode_med"] == "run-B"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_aggregate.py::test_diff_rows_cross_machine tests/test_aggregate.py::test_diff_rows_same_setup_axis_is_run -v`
Expected: FAIL — no `diff_rows`.

- [ ] **Step 3: Implement**

```python
DIM_LABELS: list[tuple[str, str]] = [
    ("chip", "Chip"), ("ram_gb", "RAM"), ("pack", "Pack"),
    ("pack_version", "Pack-Version"), ("model", "Modell"),
    ("quant", "Quant"), ("variant", "Variante"),
]


@dataclass
class CompareColumn:
    id: str
    header: str
    row: PoolRow


@dataclass
class CompareDiff:
    common: list[tuple[str, str]]
    varying: list[str]
    columns: list[CompareColumn]
    winners: dict[str, str | None]


def _num(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _winner(cols: list[CompareColumn], getter: Any, higher: bool) -> str | None:
    scored = [(getter(c.row), c.id) for c in cols if getter(c.row) is not None]
    if not scored:
        return None
    best = (max if higher else min)(v for v, _ in scored)
    leaders = [cid for v, cid in scored if v == best]
    return leaders[0] if len(leaders) == 1 else None  # tie → no trophy (B1 convention)


def diff_rows(selected: list[PoolRow]) -> CompareDiff:
    common: list[tuple[str, str]] = []
    varying: list[str] = []
    for dim, label in DIM_LABELS:
        vals = {getattr(r, dim) for r in selected}
        if len(vals) <= 1:
            common.append((dim, next(iter(vals)) if vals else ""))
        else:
            varying.append(dim)
    columns: list[CompareColumn] = []
    for r in selected:
        if varying:
            header = " · ".join(getattr(r, d) for d in varying)
        else:
            header = r.run_name  # all dims identical → the run is the axis
        columns.append(CompareColumn(id=r.id, header=header, row=r))
    winners = {
        "quality_pct": _winner(columns, lambda row: row.quality_pct, True),
        "decode_med": _winner(columns, lambda row: _num(row.decode_med), True),
        "ttft_p50": _winner(columns, lambda row: _num(row.ttft_p50), False),
        "peak_ram_gb": _winner(columns, lambda row: _num(row.peak_ram_gb), False),
        "model_delta_gb": _winner(columns, lambda row: _num(row.model_delta_gb), False),
    }
    return CompareDiff(common=common, varying=varying, columns=columns, winners=winners)
```
Add `from typing import Any` to the imports if not present.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_aggregate.py -v && uv run mypy touchstone/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add touchstone/aggregate.py tests/test_aggregate.py
git commit -m "feat(aggregate): diff_rows — auto-diff common/varying + winners"
```

---

### Task 3: `/compare` route takes `?rows=`

**Files:**
- Modify: `touchstone/gui/app.py` (`compare_cross` route, ~line 222-226)
- Test: `tests/test_gui_compare_route.py` (route-level) — NOTE: this file also holds B1 redirect tests; add to it, don't disturb them.

**Interfaces:**
- Consumes: `aggregate.pool_rows(runs_dir)`, `aggregate.diff_rows(selected)` (Tasks 1-2).
- Produces: `compare.html` context gets `pool: list[PoolRow]` (all rows) and `diff: CompareDiff | None` (only when ≥2 valid `rows` ids selected).

- [ ] **Step 1: Write the failing test**

```python
def test_compare_pool_and_diff(client_factory, tmp_path):
    # build two pool rows via real scores.csv, then select both via ?rows=
    # (reuse a scores.csv writer helper; assert pool renders and diff appears)
    ...
def test_compare_no_rows_shows_pool_only(client_factory):
    # GET /compare without ?rows → diff is None (no comparison block)
    ...
def test_compare_bad_rows_ignored(client_factory):
    # GET /compare?rows=does|not|exist → 200, no 500, no diff
    ...
```

(Use the existing GUI TestClient construction pattern from this file / `test_gui_app_detail.py`'s `_client`. For the pool, write real `scores.csv` files under the client's `runs_dir` with the Task-1 writer helper. Concrete assertions: `resp.status_code == 200`; pool body contains the run/model/variant; with two valid ids the body contains "GEMEINSAM" (the diff block marker from Task 5) — if Task 5 isn't done yet, assert the route **context** via a `render` spy like B1's Task 3, checking `diff is not None and len(diff.columns) == 2`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare_route.py -k compare_pool -v`
Expected: FAIL — route ignores `rows`, passes no `pool`/`diff`.

- [ ] **Step 3: Implement**

```python
@app.get("/compare", response_class=HTMLResponse)
def compare_cross(request: Request, rows: str | None = None) -> HTMLResponse:
    pool = aggregate_mod.pool_rows(runs_dir)
    diff = None
    if rows:
        wanted = [x for x in rows.split(",") if x]
        selected = [r for r in pool if r.id in wanted]
        # preserve the user's selection order (as given in ?rows=)
        selected.sort(key=lambda r: wanted.index(r.id))
        if len(selected) >= 2:
            diff = aggregate_mod.diff_rows(selected)
    return render("compare.html", request, pool=pool, diff=diff, active="compare")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare_route.py -v && uv run mypy touchstone/`
Expected: PASS (the rendered-HTML "GEMEINSAM" assertion goes green after Task 5; until then assert the context via spy).

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/app.py tests/test_gui_compare_route.py
git commit -m "feat(gui): /compare takes ?rows= → pool + auto-diff context"
```

---

### Task 4: `compare.html` — selection pool

**Files:**
- Modify: `touchstone/gui/templates/compare.html` (replace the body)
- Test: `tests/test_gui_compare_route.py`

**Interfaces:**
- Consumes: `pool` (list[PoolRow]) from the route context (Task 3).

- [ ] **Step 1: Write the failing test**

```python
def test_compare_pool_has_checkboxes_and_compare_button(client_factory, ...):
    body = ...  # GET /compare with ≥1 pool row
    assert 'type="checkbox"' in body
    assert "x-data" in body            # Alpine selection state
    assert "Vergleichen" in body       # the compare button
    # each pool row exposes its id as the checkbox value
    assert 'value="' in body and "|gemma|baseline" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare_route.py -k pool_has -v`
Expected: FAIL — old `compare.html` renders the static `rows` aggregate table, no checkboxes.

- [ ] **Step 3: Implement**

Replace `compare.html`'s body. Pool table over `pool`, Alpine state `{ sel: [], chip:'', pack:'', model:'' }`:
- Filter inputs (chip/pack/model) bound with `x-model`; each row `<tr x-show="(!chip || '{{r.chip}}'===chip) && …">`.
- Each row: `<input type="checkbox" value="{{ r.id }}" x-model="sel">` + cells (run_name/date, chip, ram, pack, model, quant, variant, quality%, decode, peak-ram).
- A „Vergleichen"-Button: `<button :disabled="sel.length < 2" @click="window.location = '/compare?rows=' + sel.map(encodeURIComponent).join(',')">Vergleichen (<span x-text="sel.length"></span>)</button>`.
- Keep the page title/card chrome. Put the table in a partial if it grows (`macros/_compare_pool.html`) — optional.

(Keep the existing `{% extends "base.html" %}` + `ui` macros import. German UI text.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare_route.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/compare.html tests/test_gui_compare_route.py
git commit -m "feat(gui): compare selection pool (checkboxes + filters + compare button)"
```

---

### Task 5: Auto-Diff comparison section

**Files:**
- Create: `touchstone/gui/templates/macros/_compare_diff.html`
- Modify: `touchstone/gui/templates/compare.html` (include the diff partial when `diff`)
- Test: `tests/test_gui_compare_route.py`

**Interfaces:**
- Consumes: `diff` (CompareDiff) from the route context — `diff.common`, `diff.varying`, `diff.columns` (`.id`/`.header`/`.row`), `diff.winners` (Tasks 2-3).

- [ ] **Step 1: Write the failing test**

```python
def test_compare_diff_renders_common_and_columns(client_factory, ...):
    # cross-machine: two rows differing only in chip/ram
    body = ...  # GET /compare?rows=<a>,<b>
    assert "GEMEINSAM" in body               # common block
    assert "gemma" in body                   # the constant model shown in GEMEINSAM
    assert "M1" in body and "M5" in body     # varying chips as column headers
    assert "🏆" in body                       # a winner marker on the differing metric
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare_route.py -k diff_renders -v`
Expected: FAIL — no diff partial rendered.

- [ ] **Step 3: Implement**

`macros/_compare_diff.html`: a GEMEINSAM line (`{% for dim, val in diff.common %}{{ label }} {{ val }}{% endfor %}` — map dim→label via the `ui` macros or an inline lookup) and a table:

```jinja
<div class="card">
  <div class="card-title">↔ Cross-Run-Vergleich</div>
  <p class="muted text-sm">GEMEINSAM:
    {% for dim, val in diff.common %}{% if val %}{{ val }}{% if not loop.last %} · {% endif %}{% endif %}{% endfor %}
  </p>
  <table class="bundle-table">
    <thead><tr><th></th>{% for c in diff.columns %}<th>{{ c.header }}</th>{% endfor %}</tr></thead>
    <tbody>
      <tr><td>Qualität</td>{% for c in diff.columns %}<td>{% if c.row.quality_pct is not none %}{{ "%.0f"|format(c.row.quality_pct) }} %{% if diff.winners.quality_pct == c.id %} 🏆{% endif %}{% else %}—{% endif %}</td>{% endfor %}</tr>
      <tr><td>Decode</td>{% for c in diff.columns %}<td>{% if c.row.decode_med %}{{ c.row.decode_med }} tok/s{% if diff.winners.decode_med == c.id %} 🏆{% endif %}{% else %}—{% endif %}</td>{% endfor %}</tr>
      <tr><td>TTFT P50</td>{% for c in diff.columns %}<td>{% if c.row.ttft_p50 %}{{ c.row.ttft_p50 }} s{% if diff.winners.ttft_p50 == c.id %} 🏆{% endif %}{% else %}—{% endif %}</td>{% endfor %}</tr>
      <tr><td>System-Peak</td>{% for c in diff.columns %}<td>{% if c.row.peak_ram_gb %}{{ c.row.peak_ram_gb }} GB{% if diff.winners.peak_ram_gb == c.id %} 🏆{% endif %}{% else %}—{% endif %}</td>{% endfor %}</tr>
      <tr><td>Modell-Delta</td>{% for c in diff.columns %}<td>{% if c.row.model_delta_gb %}{{ c.row.model_delta_gb }} GB{% if diff.winners.model_delta_gb == c.id %} 🏆{% endif %}{% else %}—{% endif %}</td>{% endfor %}</tr>
    </tbody>
  </table>
</div>
```
In `compare.html`, above or below the pool: `{% if diff %}{% include "macros/_compare_diff.html" %}{% endif %}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare_route.py -v`
Expected: PASS. Now the Task-3 "GEMEINSAM" HTML assertion (if used) also passes.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/macros/_compare_diff.html touchstone/gui/templates/compare.html tests/test_gui_compare_route.py
git commit -m "feat(gui): auto-diff comparison section (GEMEINSAM + varying columns + winners)"
```

---

### Task 6: Import upload field

**Files:**
- Modify: `touchstone/gui/templates/compare.html` (add an import control to the pool zone)
- Test: `tests/test_gui_compare_route.py`

**Interfaces:**
- Consumes: the existing `POST /import-bundle` endpoint (returns `{"run_dir": name}`), unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_compare_has_import_control(client_factory, ...):
    body = ...  # GET /compare
    assert 'type="file"' in body
    assert "/import-bundle" in body     # the endpoint the JS posts to
    assert "Importieren" in body        # the control label
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_compare_route.py -k import_control -v`
Expected: FAIL — no import control in compare.html.

- [ ] **Step 3: Implement**

Add an Alpine import control to compare.html's pool zone (the existing `/import-bundle` returns JSON, so post via fetch and reload on success):

```html
<div x-data="{ busy:false, err:'' }" style="margin-bottom:0.75rem">
  <label class="muted text-sm">Lauf importieren (.zip von anderer Maschine):</label>
  <input type="file" accept=".zip" @change="
    busy=true; err='';
    const fd = new FormData(); fd.append('file', $event.target.files[0]);
    fetch('/import-bundle', {method:'POST', body:fd})
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(() => window.location.reload())
      .catch(() => { err='Import fehlgeschlagen'; busy=false; });
  ">
  <span x-show="busy" class="muted text-xs">importiere…</span>
  <span x-show="err" class="text-xs" style="color:var(--nein)" x-text="err"></span>
</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_compare_route.py -v && uv run pytest -q && uv run ruff check . && uv run mypy touchstone/`
Expected: PASS, full suite green, lint+types clean.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/compare.html tests/test_gui_compare_route.py
git commit -m "feat(gui): import-bundle upload control on the compare page"
```

---

## Notes für die Umsetzung

- **`aggregate()` (CLI) niemals in Verhalten ändern** — nur den `_weighted_quality`-Helper extrahieren; die bestehenden aggregate-Tests sind der Wächter.
- **Scores.csv-Writer-Helper** für Tests: einmal definieren (Task 1) und in den Route-Tests wiederverwenden (ggf. teilen).
- Reihenfolge: Task 3 (Route-Daten) vor Task 4/5 (HTML). Task 3's Step-1 prüft den Kontext per Spy, die HTML-Strings kommen mit Task 4/5.
- `/compare/{name}` (B1-Redirect), `result.html`, `_compare_block.html` **nicht** anfassen.
- Nach jedem Task: `uv run pytest -q && uv run ruff check . && uv run mypy touchstone/` grün.
