# Judge-Meta GUI-Oberfläche Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface sub-project F's `judge-meta` round trip in the web GUI as a per-bundle "Judge-Qualität (Meta-Eval)" card: stream-only export (request `.md` + empty response `.yaml`), textarea paste-ingest → `judge_quality.md` + an inline summary.

**Architecture:** Three thin routes in `create_app()` that lazy-import the existing pure logic in `touchstone/gui/judge_meta.py` (unchanged). Two GET routes generate + stream the export artifacts on the fly (mirrors E's `/export-meta-report`); one POST route validates a pasted YAML, computes agreement + rubric, writes `judge_quality.md`, and returns an inline summary rendered by a new Jinja partial (mirrors the pack-editor's never-500 `{ok:false}` pattern). A new Alpine component drives the textarea POST. No `/compare` touch; F's logic and outputs are untouched.

**Tech Stack:** Python 3.12 · FastAPI · Jinja2 · Alpine.js · pytest (TestClient) · mypy strict · ruff.

## Global Constraints

- **Never-500 on bad input.** A parse/schema error from a pasted YAML is a normal `{"ok": false, "errors": [...]}` JSON result (HTTP 200), never an HTTP 500 — mirrors `/packs/validate`.
- **Run-dir confinement before disk access.** Every route building a path from the `{name}` segment does `rd = (runs_dir / name).resolve()` then `if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir(): raise HTTPException(status_code=404)` BEFORE touching disk.
- **Judged-gate.** `bundle_detail(rd) is None` → 404; `not detail.get("reports")` → 400 with message `"Bundle ohne Judge-Bewertung — erst touchstone judge"`. Applies to all three routes.
- **Stream-only export.** The GET routes write nothing to the bundle. Only the POST ingest writes `judge_quality.md`.
- **F logic frozen.** `touchstone/gui/judge_meta.py`, `bundles.py`, `report_md.py` are consumed unchanged — no edits. No new artifact added to either export allowlist (`judge_quality.md` is already in both).
- **Lazy import** `from touchstone.gui import judge_meta` inside each handler (mirrors `meta_report` at `app.py:269/286`).
- **Gate (every task's tests must keep these green):** `uv run pytest -q` · `uv run mypy touchstone/` · `uv run ruff check . && uv run ruff format --check .`.

## File Structure

- **Modify** `touchstone/gui/app.py` — add 3 routes + `_MAX_META_YAML` constant inside `create_app()` (the two GET routes go right after `export_meta_report` ends at `:317`, before `result()` at `:319`; the POST route + constant go in the same block).
- **Create** `touchstone/gui/templates/macros/_judge_meta_summary.html` — inline summary partial, rendered from `AgreementResult` + `RubricSummary`.
- **Modify** `touchstone/gui/templates/result.html` — new card before the Export card (`:477`), gated `{% if detail and detail.reports %}`; load the new JS in the body block.
- **Create** `touchstone/gui/static/judge_meta.js` — Alpine `judgeMeta(name)` component driving the ingest POST.
- **Create** `tests/test_gui_judge_meta_surface.py` — route + template tests (self-contained fixtures).

---

### Task 1: GET export routes (request `.md` + template `.yaml`)

**Files:**
- Modify: `touchstone/gui/app.py` (add two GET routes inside `create_app()`, after line 317)
- Test: `tests/test_gui_judge_meta_surface.py` (create)

**Interfaces:**
- Consumes: `bundles.bundle_detail(rd: Path) -> dict | None` (keys `pack`, `reports`); `judge_meta.render_request_md(detail: dict) -> str`; `judge_meta.empty_response_template(pack, cells: list[tuple[str,str]]) -> str`. `detail["reports"]` is a `list[ModelReport]` with `.model` / `.variant`.
- Produces: `GET /export-judge-meta-request/{name}` (→ `text/markdown` attachment `judge_meta_request.md`); `GET /export-judge-meta-template/{name}` (→ `application/x-yaml` attachment `judge_meta_response.yaml`).

- [ ] **Step 1: Write the failing tests + shared fixtures**

Create `tests/test_gui_judge_meta_surface.py`:

```python
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.judge import write_reports_jsonl
from touchstone.pack import load_pack
from touchstone.results import ModelReport

PACK = "packs/ndassist.yaml"


class _L:
    def spawn(self, a):
        return 1

    def alive(self, p):
        return False

    def terminate(self, p):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_L())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _judged_bundle(d: Path):
    """A minimal judged bundle (bundle.json + responses + scores + reports.jsonl)."""
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    d.mkdir(parents=True)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "host": {"chip": "M5", "ram_gb": "64 GB"},
                "date": "2026-06-24",
                "judge": {"model": "qwen3-27b"},
            }
        ),
        encoding="utf-8",
    )
    base = {
        "pack_id": pk.id, "pack_version": 1, "machine": "t", "model": "m", "quant": "q4",
        "engine": "lm-studio", "engine_version": "0", "variant": "baseline", "category": "A",
        "prompt_id": first.id, "repeat": 0, "response_text": "A.", "content_empty": False,
        "ttft_s": 0.2, "decode_tps": 30.0, "prefill_tps": 90.0, "e2e_s": 1.5,
        "prompt_tokens": 100, "completion_tokens": 50, "is_cold_start": False,
        "power_source": "ac", "peak_rss_mb": 0.0, "sys_used_mb": 20000.0,
        "mem_pressure_max": "normal", "throttled": False, "ok": True, "error": "",
        "seed": 42, "t_start": 0.0, "t_end": 1.5, "reasoning_chars": 0,
    }
    (d / "responses.jsonl").write_text(json.dumps(base) + "\n", encoding="utf-8")
    hdr = "model,variant,metric_type,metric,weight,score"
    rows = [hdr] + [f"m,baseline,dimension,{dim.id},{dim.weight},4" for dim in pk.dimensions]
    (d / "scores.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    write_reports_jsonl(
        d / "reports.jsonl",
        [
            ModelReport(
                model="m",
                variant="baseline",
                dim_scores={dim.id: 4 for dim in pk.dimensions},
                dim_rationales={pk.dimensions[0].id: "gut"},
            )
        ],
    )
    return pk


def _valid_response_yaml(pk) -> str:
    dims = "{" + ", ".join(f"{dim.id}: 4" for dim in pk.dimensions) + "}"
    crit = "\n".join(
        f"        {dim.id}: {{cites_evidence: true, names_improvement: true, "
        f"justifies_level: true, catches_safety: true}}"
        for dim in pk.dimensions
    )
    return (
        "cells:\n  - model: m\n    variant: baseline\n"
        f"    fresh_scores: {{dimensions: {dims}, ko_fired: false, overall: Ja}}\n"
        f"    critique:\n      dimensions:\n{crit}\n      summary: 'ok'\n"
        "recommendations:\n  - 'Mehr Belege zitieren.'\n"
    )


def test_export_request_streams_md_for_judged(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    r = _client(tmp_path).get(f"/export-judge-meta-request/{d.name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "judge_meta_request.md" in r.headers["content-disposition"]
    assert "Teil A" in r.text and "Teil B" in r.text


def test_export_request_refuses_unjudged(tmp_path):
    pk = load_pack(PACK)
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps({"pack_id": pk.id, "pack_path": PACK, "host": {}, "date": "x"}),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")  # no reports.jsonl → unjudged
    r = _client(tmp_path).get(f"/export-judge-meta-request/{d.name}")
    assert r.status_code == 400


def test_export_request_traversal_404(tmp_path):
    _judged_bundle(tmp_path / "2026_eval_nd")
    r = _client(tmp_path).get("/export-judge-meta-request/..%2f..%2fetc")
    assert r.status_code == 404


def test_export_template_streams_yaml_round_trips(tmp_path):
    from touchstone.gui import judge_meta

    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    r = _client(tmp_path).get(f"/export-judge-meta-template/{d.name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-yaml")
    assert "judge_meta_response.yaml" in r.headers["content-disposition"]
    parsed = judge_meta.parse_meta_response(r.text)  # must round-trip
    assert any(c.model == "m" and c.variant == "baseline" for c in parsed.cells)


def test_export_template_refuses_unjudged(tmp_path):
    pk = load_pack(PACK)
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps({"pack_id": pk.id, "pack_path": PACK, "host": {}, "date": "x"}),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    r = _client(tmp_path).get(f"/export-judge-meta-template/{d.name}")
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_judge_meta_surface.py -q`
Expected: FAIL — routes return 404 (not registered yet); `test_export_request_traversal_404` may pass incidentally.

- [ ] **Step 3: Add the two GET routes**

In `touchstone/gui/app.py`, immediately after the `export_meta_report` function ends (after line 317, before `@app.get("/result/{name}", ...)` at line 319), insert:

```python
    @app.get("/export-judge-meta-request/{name}")
    def export_judge_meta_request(name: str) -> Any:
        """Stream the judge-quality request markdown (Teil A blank fresh-scoring + Teil B the
        local judge's rationales) for a judged bundle — to hand to an external cloud AI.
        Stream-only: nothing is written to the bundle (the filled response returns via ingest)."""
        from touchstone.gui import judge_meta

        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
            raise HTTPException(status_code=404)
        detail = bundles.bundle_detail(rd)
        if detail is None:
            raise HTTPException(status_code=404)
        if not detail.get("reports"):
            raise HTTPException(
                status_code=400, detail="Bundle ohne Judge-Bewertung — erst touchstone judge"
            )
        md = judge_meta.render_request_md(detail)
        return Response(
            md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="judge_meta_request.md"'},
        )

    @app.get("/export-judge-meta-template/{name}")
    def export_judge_meta_template(name: str) -> Any:
        """Stream the empty judge_meta_response.yaml skeleton (one entry per cell × pack
        dimension) the cloud AI fills. Stream-only."""
        from touchstone.gui import judge_meta

        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
            raise HTTPException(status_code=404)
        detail = bundles.bundle_detail(rd)
        if detail is None:
            raise HTTPException(status_code=404)
        if not detail.get("reports"):
            raise HTTPException(
                status_code=400, detail="Bundle ohne Judge-Bewertung — erst touchstone judge"
            )
        cells = sorted({(r.model, r.variant) for r in detail["reports"]})
        body = judge_meta.empty_response_template(detail["pack"], cells)
        return Response(
            body,
            media_type="application/x-yaml",
            headers={"Content-Disposition": 'attachment; filename="judge_meta_response.yaml"'},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_judge_meta_surface.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/app.py tests/test_gui_judge_meta_surface.py
git commit -m "feat(gui): judge-meta export routes (request md + template yaml, stream-only)"
```

---

### Task 2: POST ingest route + inline-summary partial

**Files:**
- Modify: `touchstone/gui/app.py` (add `_MAX_META_YAML` + POST route inside `create_app()`, after the Task 1 routes)
- Create: `touchstone/gui/templates/macros/_judge_meta_summary.html`
- Test: `tests/test_gui_judge_meta_surface.py` (add tests)

**Interfaces:**
- Consumes: `judge_meta.parse_meta_response(text) -> MetaResponse` (raises `ValueError`/pydantic `ValidationError` on bad input); `judge_meta.compute_agreement(pack, reports, master_rows, fresh) -> AgreementResult`; `judge_meta.aggregate_rubric(reports, fresh) -> RubricSummary`; `judge_meta.render_judge_quality_md(detail, agreement, rubric, response) -> str`. `AgreementResult` has `.cells: list[CellAgreement]` (each `.model`, `.variant`, `.dims: list[DimAgreement]` with `.outlier: bool`, `.local_quality_pct`, `.cloud_quality_pct`, `.quality_delta`, `.ko_concordant`) and `.bundle_mean_abs_delta: float | None`. `RubricSummary` has `.cites_evidence`, `.names_improvement`, `.justifies_level`, `.catches_safety`, each a `tuple[int, int]`.
- Produces: `POST /judge-meta-ingest/{name}` (`yaml_text: str = Form(...)`) → `{"ok": false, "errors": [...]}` | `{"ok": true, "summary_html": str, "download_url": str}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_judge_meta_surface.py`:

```python
def test_ingest_valid_writes_and_returns_summary(tmp_path):
    d = tmp_path / "2026_eval_nd"
    pk = _judged_bundle(d)
    r = _client(tmp_path).post(
        f"/judge-meta-ingest/{d.name}", data={"yaml_text": _valid_response_yaml(pk)}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["download_url"] == f"/export/{d.name}/judge_quality.md"
    assert "Begründungs-Qualität" in body["summary_html"]
    assert (d / "judge_quality.md").exists()
    assert "## Headline" in (d / "judge_quality.md").read_text(encoding="utf-8")


def test_ingest_malformed_yaml_returns_errors_not_500(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    # a cell missing required fresh_scores/critique → pydantic ValidationError
    r = _client(tmp_path).post(
        f"/judge-meta-ingest/{d.name}",
        data={"yaml_text": "cells:\n  - model: m\n    variant: baseline\n"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["errors"]


def test_ingest_non_mapping_yaml_returns_errors(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    r = _client(tmp_path).post(
        f"/judge-meta-ingest/{d.name}", data={"yaml_text": "- just\n- a\n- list\n"}
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_ingest_unjudged_400(tmp_path):
    pk = load_pack(PACK)
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps({"pack_id": pk.id, "pack_path": PACK, "host": {}, "date": "x"}),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    r = _client(tmp_path).post(f"/judge-meta-ingest/{d.name}", data={"yaml_text": "cells: []\n"})
    assert r.status_code == 400


def test_ingest_oversize_400(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    r = _client(tmp_path).post(
        f"/judge-meta-ingest/{d.name}", data={"yaml_text": "x" * 1_000_001}
    )
    assert r.status_code == 400


def test_ingest_traversal_404(tmp_path):
    _judged_bundle(tmp_path / "2026_eval_nd")
    r = _client(tmp_path).post(
        "/judge-meta-ingest/..%2f..%2fetc", data={"yaml_text": "cells: []\n"}
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_judge_meta_surface.py -q -k ingest`
Expected: FAIL — route not registered (404 for all).

- [ ] **Step 3: Create the inline-summary partial**

Create `touchstone/gui/templates/macros/_judge_meta_summary.html`:

```html
{# Inline judge-quality summary rendered from AgreementResult + RubricSummary (no MD→HTML).
   Shown in the result page's Judge-Qualität card after a successful ingest. #}
{% set ns = namespace(outliers=0) %}
{% for c in agreement.cells %}{% for d in c.dims if d.outlier %}{% set ns.outliers = ns.outliers + 1 %}{% endfor %}{% endfor %}
<div class="text-sm" style="margin-bottom:0.5rem">
  <strong>mean|Δ| zum Referenz-Judge:</strong>
  {{ agreement.bundle_mean_abs_delta if agreement.bundle_mean_abs_delta is not none else "—" }}
  · <strong>{{ ns.outliers }}</strong> Ausreißer 🚩
</div>
<table class="text-xs" style="width:100%; border-collapse:collapse">
  <thead>
    <tr style="text-align:left; border-bottom:1px solid var(--border)">
      <th>Zelle</th><th>Quality% lokal</th><th>Cloud</th><th>Δ</th><th>K.-o.</th>
    </tr>
  </thead>
  <tbody>
    {% for c in agreement.cells %}
    <tr>
      <td>{{ c.model }} · {{ c.variant }}</td>
      <td>{{ c.local_quality_pct if c.local_quality_pct is not none else "—" }}</td>
      <td>{{ c.cloud_quality_pct if c.cloud_quality_pct is not none else "—" }}</td>
      <td>{{ c.quality_delta if c.quality_delta is not none else "—" }}</td>
      <td>{% if c.ko_concordant %}✓{% else %}✗{% endif %}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
<div class="text-xs muted" style="margin-top:0.5rem">
  Begründungs-Qualität —
  Belege: {{ rubric.cites_evidence[0] }}/{{ rubric.cites_evidence[1] }} ·
  Verbesserung benannt (&lt;5): {{ rubric.names_improvement[0] }}/{{ rubric.names_improvement[1] }} ·
  Höhe begründet: {{ rubric.justifies_level[0] }}/{{ rubric.justifies_level[1] }} ·
  Sicherheit: {{ rubric.catches_safety[0] }}/{{ rubric.catches_safety[1] }}
</div>
```

- [ ] **Step 4: Add the POST ingest route**

In `touchstone/gui/app.py`, immediately after the `export_judge_meta_template` route from Task 1, insert:

```python
    _MAX_META_YAML = 1_000_000  # a response YAML over ~1 MB is abuse, not a use case

    @app.post("/judge-meta-ingest/{name}")
    def judge_meta_ingest(name: str, yaml_text: str = Form(...)) -> dict[str, Any]:
        """Validate + ingest a pasted judge_meta_response.yaml: compute agreement + rubric, write
        judge_quality.md into the bundle, return an inline summary. Never 500s — a parse/schema
        error is a normal {ok:false} result (pack-editor pattern)."""
        from touchstone.gui import judge_meta

        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
            raise HTTPException(status_code=404)
        if len(yaml_text) > _MAX_META_YAML:
            raise HTTPException(status_code=400, detail="response YAML too large")
        detail = bundles.bundle_detail(rd)
        if detail is None:
            raise HTTPException(status_code=404)
        if not detail.get("reports"):
            raise HTTPException(
                status_code=400, detail="Bundle ohne Judge-Bewertung — erst touchstone judge"
            )
        try:
            meta = judge_meta.parse_meta_response(yaml_text)
        except Exception as exc:
            return {"ok": False, "errors": [str(exc)]}
        agreement = judge_meta.compute_agreement(
            detail["pack"], detail["reports"], detail["master_rows"], meta
        )
        rubric = judge_meta.aggregate_rubric(detail["reports"], meta)
        md = judge_meta.render_judge_quality_md(detail, agreement, rubric, meta)
        (rd / "judge_quality.md").write_text(md, encoding="utf-8")
        summary_html = _templates.get_template("macros/_judge_meta_summary.html").render(
            agreement=agreement, rubric=rubric
        )
        return {
            "ok": True,
            "summary_html": summary_html,
            "download_url": f"/export/{name}/judge_quality.md",
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_judge_meta_surface.py -q`
Expected: PASS (11 tests).

- [ ] **Step 6: Commit**

```bash
git add touchstone/gui/app.py touchstone/gui/templates/macros/_judge_meta_summary.html tests/test_gui_judge_meta_surface.py
git commit -m "feat(gui): judge-meta ingest route + inline-summary partial (never-500 paste)"
```

---

### Task 3: Result-page card + Alpine component

**Files:**
- Modify: `touchstone/gui/templates/result.html` (load JS in body block; add card before Export card at `:477`)
- Create: `touchstone/gui/static/judge_meta.js`
- Test: `tests/test_gui_judge_meta_surface.py` (add template tests)

**Interfaces:**
- Consumes: the three routes from Tasks 1–2; `detail.reports` (gate); `run_dir.name`; `(run_dir / "judge_quality.md").exists()`.
- Produces: rendered card HTML on `/result/{name}` for judged bundles only.

- [ ] **Step 1: Write the failing template tests**

Append to `tests/test_gui_judge_meta_surface.py`:

```python
def test_result_page_shows_judge_meta_card_when_judged(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert r.status_code == 200
    assert "Judge-Qualität (Meta-Eval)" in r.text
    assert f"/export-judge-meta-request/{d.name}" in r.text
    assert f"/export-judge-meta-template/{d.name}" in r.text


def test_result_page_hides_judge_meta_card_when_unjudged(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    (d / "reports.jsonl").unlink()  # strip the judging → eval-only bundle
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert r.status_code == 200
    assert "Judge-Qualität (Meta-Eval)" not in r.text


def test_result_page_shows_download_when_quality_exists(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    (d / "judge_quality.md").write_text("# Judge-Qualität\n", encoding="utf-8")
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert f"/export/{d.name}/judge_quality.md" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_judge_meta_surface.py -q -k result_page`
Expected: FAIL — card markup not present yet.

- [ ] **Step 3: Create the Alpine component**

Create `touchstone/gui/static/judge_meta.js`:

```javascript
// touchstone/gui/static/judge_meta.js
// Alpine component for the per-bundle judge-quality meta-eval card. A YAML textarea → POST
// /judge-meta-ingest/{name} → server validates (parse_meta_response), writes judge_quality.md,
// returns an inline summary. Never alerts; a parse/schema error is a normal {ok:false} result.
// Build-free; registered on alpine:init, loaded non-deferred.
"use strict";

document.addEventListener("alpine:init", () => {
  Alpine.data("judgeMeta", (name) => ({
    name: name,
    yamlText: "",
    ok: null, // null = not yet submitted; true/false after a POST
    errors: [],
    summaryHtml: "",
    downloadUrl: "",
    busy: false,
    async ingest() {
      this.busy = true;
      this.ok = null;
      this.errors = [];
      try {
        const res = await fetch(`/judge-meta-ingest/${encodeURIComponent(this.name)}`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ yaml_text: this.yamlText }),
        });
        if (!res.ok) {
          // 400/404 (oversize / unjudged / traversal) — surface the server detail.
          this.ok = false;
          const d = await res.json().catch(() => ({}));
          this.errors = [d.detail || `Fehler (${res.status})`];
          return;
        }
        const data = await res.json();
        this.ok = data.ok;
        this.errors = data.errors || [];
        this.summaryHtml = data.summary_html || "";
        this.downloadUrl = data.download_url || "";
      } catch (e) {
        this.ok = false;
        this.errors = ["Auswertung fehlgeschlagen (Netz)"];
      } finally {
        this.busy = false;
      }
    },
  }));
});
```

- [ ] **Step 4: Load the JS in result.html**

In `touchstone/gui/templates/result.html`, change the top of the body block (lines 7-8) from:

```html
{% block body %}
<script src="/static/sparkline.js" defer></script>
```

to:

```html
{% block body %}
<script src="/static/sparkline.js" defer></script>
<!-- non-deferred so judgeMeta is registered before the deferred Alpine fires alpine:init -->
<script src="/static/judge_meta.js"></script>
```

- [ ] **Step 5: Add the card before the Export card**

In `touchstone/gui/templates/result.html`, immediately before the `{# ── Export ── #}` comment at line 477, insert:

```html
{# ── Judge-Qualität (Meta-Eval) ────────────────────────────────────────────── #}
{% if detail and detail.reports %}
<div class="card" x-data="judgeMeta('{{ run_dir.name }}')">
  <div class="card-title">Judge-Qualität (Meta-Eval)</div>
  <p class="muted text-sm" style="margin-bottom:0.5rem">
    Lässt eine externe Cloud-KI die Qualität des lokalen Judges prüfen (Kalibrierung +
    Begründungs-Güte). Teil A (eigene frische Bewertung) <strong>zuerst</strong> ausfüllen, dann Teil B.
  </p>
  <div class="flex gap-2" style="margin-bottom:0.75rem; flex-wrap:wrap">
    <a href="/export-judge-meta-request/{{ run_dir.name }}" class="btn btn-primary"
       title="Anfrage-Markdown für die Cloud-KI (Teil A blank + Teil B lokale Begründungen).">Anfrage (.md)</a>
    <a href="/export-judge-meta-template/{{ run_dir.name }}" class="btn btn-secondary"
       title="Leere Antwort-Vorlage (YAML) zum Ausfüllen durch die Cloud-KI.">Antwort-Vorlage (.yaml)</a>
    {% if (run_dir / "judge_quality.md").exists() %}
    <a href="/export/{{ run_dir.name }}/judge_quality.md" class="btn btn-secondary"
       title="Bereits ausgewertet — Neu-Einlesen überschreibt.">judge_quality.md</a>
    {% endif %}
  </div>
  <label class="text-sm" style="display:block; margin-bottom:0.25rem">
    Ausgefüllte Antwort (YAML) einfügen:
  </label>
  <textarea x-model="yamlText" rows="8" spellcheck="false"
            style="width:100%; font-family:monospace; font-size:0.8rem; white-space:pre; overflow:auto"
            placeholder="cells:&#10;  - model: …"></textarea>
  <div class="flex gap-2 items-center" style="margin-top:0.5rem">
    <button type="button" class="btn btn-primary" @click="ingest()" :disabled="busy"
            x-text="busy ? 'Werte aus…' : 'Auswerten'"></button>
    <span class="text-xs" style="color:var(--ja)" x-show="ok === true" x-cloak>✓ judge_quality.md geschrieben</span>
  </div>
  <template x-if="ok === false">
    <ul class="text-xs" style="color:var(--nein); margin:0.5rem 0 0 1rem">
      <template x-for="e in errors" :key="e"><li x-text="e"></li></template>
    </ul>
  </template>
  <div x-show="ok === true" x-cloak style="margin-top:0.75rem" x-html="summaryHtml"></div>
  <div x-show="ok === true" x-cloak style="margin-top:0.5rem">
    <a class="btn btn-secondary" :href="downloadUrl">judge_quality.md herunterladen</a>
  </div>
</div>
{% endif %}

```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_judge_meta_surface.py -q`
Expected: PASS (14 tests). (Note: `judge_meta.js` execution is not unit-tested — no JS harness in this repo, mirroring `pack_editor.js`; it is covered by the Task 4 headless smoke.)

- [ ] **Step 7: Commit**

```bash
git add touchstone/gui/templates/result.html touchstone/gui/static/judge_meta.js tests/test_gui_judge_meta_surface.py
git commit -m "feat(gui): judge-meta card on result page (textarea paste-ingest + inline summary)"
```

---

### Task 4: Full gate + headless smoke (exit criteria)

**Files:** none (verification only).

- [ ] **Step 1: Full test suite**

Run: `uv run pytest -q`
Expected: PASS — all pre-existing tests + the 14 new ones green.

- [ ] **Step 2: Type check**

Run: `uv run mypy touchstone/`
Expected: no errors (strict).

- [ ] **Step 3: Lint + format**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: both clean. (If `ruff format --check .` reports a file, run `uv run ruff format .` and amend the relevant commit.)

- [ ] **Step 4: Headless smoke (GUI restarted)**

Start a fresh GUI server against a judged bundle's runs dir, then verify each route end-to-end with curl (the running server must be the new code — restart it, per the project's "GUI nach Änderung neu starten" lesson):

```bash
# in one shell: start the server (note the bound port from output / use a fixed one)
uv run touchstone gui &   # or: uv run python -m touchstone gui
# then, against a real judged bundle <name> under runs/:
curl -s "http://127.0.0.1:<port>/export-judge-meta-request/<name>" | head -5      # MD with "Teil A"
curl -s "http://127.0.0.1:<port>/export-judge-meta-template/<name>" | head -5     # YAML skeleton
# fill the template, then:
curl -s -X POST "http://127.0.0.1:<port>/judge-meta-ingest/<name>" \
     --data-urlencode "yaml_text@filled_response.yaml" | python -m json.tool       # {ok:true, summary_html, download_url}
# confirm the card renders + the textarea/Auswerten flow works in a browser on /result/<name>
```

Expected: the request streams MD beginning with the two-part instruction; the template streams a valid YAML skeleton; the POST returns `{"ok": true, ...}` and writes `judge_quality.md`; the browser card shows the inline summary + download button.

- [ ] **Step 5: Adversarial whole-branch review (controller, 3 lenses) → fix confirmed findings → merge**

Review the whole branch focusing on: (1) confinement/traversal at **all three** routes (resolve + `is_relative_to` before any disk access); (2) the never-500 path (malformed/non-mapping YAML → `{ok:false}`, not 500); (3) the judged-gate holds everywhere (no render/write on an unjudged bundle, card hidden in template). Verify each finding independently before fixing. Then merge `feat/judge-meta-gui` → `main` + push (no PR, solo repo).

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- Heimat / card before Export, gated on `detail.reports` → Task 3 (Steps 5, and the hide-test).
- Stream-only export (request + template) → Task 1.
- Textarea paste-ingest, never-500, writes `judge_quality.md`, inline summary → Task 2 + Task 3.
- `/export/{name}/judge_quality.md` reused for download → Task 2 (`download_url`) + Task 3 (existing-file button); allowlist unchanged (Global Constraints).
- Fehlerbehandlung (unjudged 400, traversal 404, malformed `{ok:false}`, oversize 400, CSRF auto) → Tasks 1–2 tests.
- Testing (route + template + gate + smoke + adversarial) → Tasks 1–4.

**2. Placeholder scan** — no "TBD/TODO/handle edge cases"; every code step shows complete code; `<port>`/`<name>` in Step 4 Task 4 are runtime values for a manual smoke, not code placeholders.

**3. Type consistency** — route names (`export_judge_meta_request`, `export_judge_meta_template`, `judge_meta_ingest`) and URLs are identical across tasks; `_MAX_META_YAML = 1_000_000` matches the oversize test's `1_000_001`; `summary_html`/`download_url`/`ok`/`errors` keys match between the POST route, the JS component, and the tests; partial consumes `agreement`/`rubric` exactly as the route renders them; `AgreementResult`/`RubricSummary`/`CellAgreement` field names match `judge_meta.py` (`bundle_mean_abs_delta`, `local_quality_pct`, `cloud_quality_pct`, `quality_delta`, `ko_concordant`, `cites_evidence`/`names_improvement`/`justifies_level`/`catches_safety` tuples).
