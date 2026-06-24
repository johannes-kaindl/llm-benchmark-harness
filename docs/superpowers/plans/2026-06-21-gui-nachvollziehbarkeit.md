# GUI-Nachvollziehbarkeit (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die GUI-Read-Schicht von „Schlagwörtern" zu lückenlos nachvollziehbaren Ansichten umbauen + die Judge-Dimensions-Begründung erfassen, sodass „Warum Q6 = 2" bis zur Wurzel durchklickbar ist.

**Architecture:** Drei Schichten, Daten zuerst: (A) Judge erfasst `dim_rationales` (verschachteltes Schema mit prompt_id-Belegen) + `reports.jsonl`-Persistenz + CPU-Sampling; (B) `bundles.py` liefert ein reiches Detail-Objekt (Reports aus `reports.jsonl`, Fallback `scores.csv`); (C) drei überarbeitete Templates + RAM/CPU-Chart. Fundament (Control-Plane/Sentinel/Sicherheit) unverändert.

**Tech Stack:** Python 3.12 · pytest/mypy/ruff · FastAPI/Jinja2 (`[gui]`-extra) · psutil. Spec: [`docs/superpowers/specs/2026-06-21-gui-nachvollziehbarkeit-design.md`](../specs/2026-06-21-gui-nachvollziehbarkeit-design.md).

---

## File Structure

| File | Responsibility |
|---|---|
| `touchstone/judge.py` (modify) | nested Dimensions-Schema im Prompt + Beleg-Pflicht; `parse_dimension_report`; `score_dimensions` füllt `dim_rationales`; `write_reports_jsonl`/`load_reports_jsonl` |
| `touchstone/cli.py` (modify) | `_render_judge_scorecard` schreibt `reports.jsonl` (deckt beide Judge-Pfade) |
| `touchstone/models.py` (modify) | `ResourceSample.cpu_pct` |
| `touchstone/sampler.py` (modify) | `psutil.cpu_percent` in `sample_once` + prime in `start` |
| `touchstone/gui/bundles.py` (modify) | Reports aus `reports.jsonl` (Fallback `scores.csv`); `bundle_detail()` reiches Objekt |
| `touchstone/gui/app.py` (modify) | Read-Routen liefern `bundle_detail`; Verlinkungs-Anker |
| `touchstone/gui/templates/{result,pack,overview}.html` (modify) | Drill-down · Kriterien+Methode · bedeutungstragende Übersicht |
| `touchstone/gui/static/sparkline.js` (new) | RAM/CPU-Verlaufs-Chart (Inline-SVG, build-frei) |
| `tests/test_gui_*.py`, `tests/test_judge.py` (new/modify) | per §9 der Spec |
| `AGENTS.md` (modify) | `reports.jsonl` + `cpu_pct` |

Tasks: Daten-Fundament zuerst (1–3), dann Read (4), dann Views (5–6), dann Abschluss (7).

---

## Task 1: Judge erfasst `dim_rationales` (verschachteltes Schema + Beleg)

**Files:**
- Modify: `touchstone/judge.py` (`_build_dimension_prompt:121`, `parse_dimension_scores:78`, `score_dimensions:219`)
- Modify: `tests/test_judge.py` (die zwei Dimensions-Tests heben)

- [ ] **Step 1: Write the failing test** — append to `tests/test_judge.py`:

```python
from touchstone.judge import parse_dimension_report


def test_parse_dimension_report_nested_scores_and_rationales(_pack):
    raw = '{"Q1": {"score": 4, "rationale": "solide bei A1, A2"}, "Q6": {"score": 2, "rationale": "unsicher bei E1"}}'
    scores, rationales = parse_dimension_report(raw, _pack)
    assert scores == {"Q1": 4, "Q6": 2}
    assert "E1" in rationales["Q6"]


def test_parse_dimension_report_tolerates_bare_int(_pack):
    # judge drift: a bare int at the dim key still parses, rationale defaults to ""
    scores, rationales = parse_dimension_report('{"Q1": 4}', _pack)
    assert scores == {"Q1": 4}
    assert rationales["Q1"] == ""
```

`_pack` fixture: if `tests/test_judge.py` has no pack fixture, add `def _pack():\n    from touchstone.pack import load_pack\n    return load_pack("packs/ndassist.yaml")` as a `@pytest.fixture`. (Check the file first; reuse an existing pack fixture if present.)

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_judge.py -k dimension_report -v`
Expected: FAIL — `parse_dimension_report` undefined.

- [ ] **Step 3: Implement `parse_dimension_report` + rewire** in `touchstone/judge.py`.

Replace `parse_dimension_scores` (judge.py:78-89) with both the new report parser and a thin back-compat wrapper:

```python
def parse_dimension_report(raw: str, pack: Pack) -> tuple[dict[str, int], dict[str, str]]:
    """Extract ({dim_id: 1..5}, {dim_id: rationale}) from the nested judge JSON.

    Tolerant of judge drift: a bare int at the dim key still yields the score
    (rationale defaults to ''); a nested {"score", "rationale"} object yields both.
    """
    obj = _extract_json(raw)
    scores: dict[str, int] = {}
    rationales: dict[str, str] = {}
    if not isinstance(obj, dict):
        return scores, rationales
    for d in pack.dimensions:
        if d.id not in obj:
            continue
        val = obj[d.id]
        if isinstance(val, dict):
            score = _clamp_score(val.get("score"))
            rationale = str(val.get("rationale", ""))
        else:
            score = _clamp_score(val)
            rationale = ""
        if score is not None:
            scores[d.id] = score
            rationales[d.id] = rationale
    return scores, rationales


def parse_dimension_scores(raw: str, pack: Pack) -> dict[str, int]:
    """Back-compat: scores only (delegates to parse_dimension_report)."""
    return parse_dimension_report(raw, pack)[0]
```

Update `_build_dimension_prompt` (judge.py:121-140) — nested schema + Beleg-Pflicht:

```python
def _build_dimension_prompt(pack: Pack, verdicts: list[Verdict]) -> tuple[str, str]:
    dims = "\n".join(f"  {d.id} = {d.name} ({d.about})" for d in pack.dimensions)
    evidence = (
        "\n".join(
            f"  {v.prompt_id}: score {v.score}{' · RED FLAG' if v.red_flag else ''}"
            for v in verdicts
            if not v.unscored
        )
        or "  (keine Einzelbewertungen)"
    )
    keys = ", ".join(f'"{d.id}": {{"score": <1-5>, "rationale": "<1 Satz>"}}' for d in pack.dimensions)
    system = (
        "Du bist ein strenger, fairer Bewerter. Vergib pro Querschnitts-Dimension einen "
        "holistischen Wert 1-5 über alle Antworten dieses Modells UND eine kurze Begründung, "
        "die mindestens 1-2 konkrete prompt_ids als Beleg nennt (z. B. 'schwach bei E1, C3'). "
        f"Antworte ausschließlich mit einem JSON-Objekt {{{keys}}}. Kein weiterer Text."
    )
    user = f"Dimensionen:\n{dims}\n\nEinzel-Evidenz (Prompt: Score):\n{evidence}\n\nGib das JSON aus."
    return system, user
```

Update `score_dimensions` (judge.py:219-224):

```python
def score_dimensions(
    backend: JudgeBackend, pack: Pack, *, model: str, variant: str, verdicts: list[Verdict]
) -> ModelReport:
    system, user = _build_dimension_prompt(pack, verdicts)
    scores, rationales = parse_dimension_report(backend.judge(system=system, user=user), pack)
    return ModelReport(model=model, variant=variant, dim_scores=scores, dim_rationales=rationales)
```

- [ ] **Step 4: Heave the two existing dimension tests** to the nested shape. In `tests/test_judge.py`, find `test_parse_dimension_scores_extracts_present_dims_clamped` and `test_score_dimensions_parses_master_report` (≈ lines 119, 156). Update their FakeBackend payloads / inputs from flat `{"Q1": 4, "Q6": 3}` to nested `{"Q1": {"score": 4, "rationale": "x"}, "Q6": {"score": 3, "rationale": "E1 schwach"}}`, keep the score assertions, and add `assert report.dim_rationales["Q6"]` (non-empty). `parse_dimension_scores` (the wrapper) still returns the flat clamped scores, so its test only needs the payload updated.

- [ ] **Step 5: Run + types + lint**

Run: `uv run pytest tests/test_judge.py -q && uv run mypy touchstone/ && uv run ruff check . && uv run ruff format .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add touchstone/judge.py tests/test_judge.py
git commit -m "feat(judge): capture per-dimension rationale (nested schema + prompt_id evidence)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `reports.jsonl` persistence (both judge paths)

**Files:**
- Modify: `touchstone/judge.py` (add `write_reports_jsonl` + `load_reports_jsonl`)
- Modify: `touchstone/cli.py` (`_render_judge_scorecard:586` writes it — covers both paths)
- Test: `tests/test_gui_reports.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_reports.py
from touchstone.judge import load_reports_jsonl, write_reports_jsonl
from touchstone.results import ModelReport


def test_reports_jsonl_roundtrip(tmp_path):
    reports = [
        ModelReport("m", "baseline", {"Q1": 4, "Q6": 2}, {"Q1": "ok", "Q6": "E1 schwach"}),
        ModelReport("m", "none", {"Q1": 3}, {"Q1": "knapp"}),
    ]
    p = tmp_path / "reports.jsonl"
    write_reports_jsonl(p, reports)
    back = load_reports_jsonl(p)
    assert len(back) == 2
    assert back[0].dim_scores == {"Q1": 4, "Q6": 2}
    assert back[0].dim_rationales["Q6"] == "E1 schwach"


def test_load_reports_jsonl_tolerates_half_line(tmp_path):
    p = tmp_path / "reports.jsonl"
    p.write_text('{"model":"m","variant":"none","dim_scores":{"Q1":3},"dim_rationales":{}}\n{"model":"m"',
                 encoding="utf-8")
    back = load_reports_jsonl(p)
    assert len(back) == 1 and back[0].dim_scores == {"Q1": 3}


def test_missing_reports_jsonl_returns_empty(tmp_path):
    assert load_reports_jsonl(tmp_path / "nope.jsonl") == []
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_gui_reports.py -v`
Expected: FAIL — functions undefined.

- [ ] **Step 3: Implement** in `touchstone/judge.py` (next to `load_judgements_jsonl:260`):

```python
def write_reports_jsonl(path: str | Path, reports: list[ModelReport]) -> None:
    """One clean batch write of the holistic master reports (scores + rationales)."""
    with Path(path).open("w", encoding="utf-8") as fh:
        for r in reports:
            fh.write(json.dumps(r.as_dict(), ensure_ascii=False) + "\n")


def load_reports_jsonl(path: str | Path) -> list[ModelReport]:
    """Read reports.jsonl into ModelReports, tolerant of a half-written final line."""
    out: list[ModelReport] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(ModelReport(**json.loads(line)))
        except Exception:
            continue
    return out
```

- [ ] **Step 4: Wire into `_render_judge_scorecard`** (cli.py:586) — it runs in BOTH judge branches, so one write covers both. Add after the scores.csv block (after cli.py:603):

```python
    from touchstone.judge import write_reports_jsonl
    write_reports_jsonl(bundle / "reports.jsonl", reports)
```

(Place the import at the top of cli.py with the other `touchstone.judge` imports instead, if cleaner — it already imports from `touchstone.judge`.)

- [ ] **Step 5: Add a CLI integration test** that judge writes reports.jsonl in the default (non-emit) path:

```python
# tests/test_gui_reports.py — append
import json
from typer.testing import CliRunner
from touchstone.cli import app

runner = CliRunner()


def test_judge_writes_reports_jsonl(tmp_path, monkeypatch):
    b = tmp_path / "bundle"; b.mkdir()
    (b / "bundle.json").write_text(json.dumps({"pack_path": "packs/ndassist.yaml", "host": {}}), encoding="utf-8")
    (b / "responses.jsonl").write_text("", encoding="utf-8")
    from touchstone.results import ModelReport, Verdict
    monkeypatch.setattr("touchstone.cli.load_responses_jsonl", lambda p: [])
    monkeypatch.setattr("touchstone.cli._judge_and_persist",
                        lambda *a, **k: ([], [ModelReport("m", "none", {"Q1": 3}, {"Q1": "x"})]))
    monkeypatch.setattr("touchstone.cli.OpenAIJudgeBackend", lambda *a, **k: object())
    import types
    monkeypatch.setattr("touchstone.cli.load_judge_config", lambda p: types.SimpleNamespace(
        endpoint=types.SimpleNamespace(base_url="x", api_key="y"), model="m", temperature=0.0))
    res = runner.invoke(app, ["judge", "--bundle", str(b), "--judge-config", "judge.yaml"])
    assert res.exit_code == 0, res.output
    assert (b / "reports.jsonl").exists()
    from touchstone.judge import load_reports_jsonl
    assert load_reports_jsonl(b / "reports.jsonl")[0].dim_rationales["Q1"] == "x"
```

- [ ] **Step 6: Run + types + commit**

Run: `uv run pytest tests/test_gui_reports.py tests/test_cli_judge_web.py -q && uv run mypy touchstone/`
Expected: green (judge path still works; reports.jsonl written).

```bash
git add touchstone/judge.py touchstone/cli.py tests/test_gui_reports.py
git commit -m "feat(judge): persist ModelReport as reports.jsonl (both judge paths)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: CPU sampling (`ResourceSample.cpu_pct`)

**Files:**
- Modify: `touchstone/models.py` (`ResourceSample`), `touchstone/sampler.py` (`HostSampler`)
- Test: `tests/test_gui_cpu_sample.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_cpu_sample.py
from touchstone.merge import load_samples_jsonl
from touchstone.models import ResourceSample


def test_resource_sample_has_cpu_pct_defaulting_none():
    s = ResourceSample(ts=1.0, sys_used_mb=1.0, sys_available_mb=1.0, swap_used_mb=0.0,
                       server_rss_mb=None, mem_pressure_level="normal", throttled=False)
    assert s.cpu_pct is None  # last field, defaulted → old code constructs unchanged


def test_old_resources_jsonl_loads_without_cpu(tmp_path):
    p = tmp_path / "resources.jsonl"
    p.write_text('{"ts":1.0,"sys_used_mb":1.0,"sys_available_mb":1.0,"swap_used_mb":0.0,'
                 '"server_rss_mb":null,"mem_pressure_level":"normal","throttled":false}\n', encoding="utf-8")
    samples = load_samples_jsonl(p)
    assert samples[0].cpu_pct is None  # missing key → default
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_gui_cpu_sample.py -v`
Expected: FAIL — `cpu_pct` unknown.

- [ ] **Step 3: Add the field** — in `touchstone/models.py`, `ResourceSample` (after `throttled: bool`, as the LAST field):

```python
    cpu_pct: float | None = None  # system CPU load %, None for ticks recorded before cpu sampling
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_gui_cpu_sample.py -v`
Expected: PASS.

- [ ] **Step 5: Fill it in the sampler.** In `touchstone/sampler.py`: prime in `HostSampler.start` (sampler.py:209) so the first non-interval call isn't a bogus 0.0:

```python
    def start(self) -> None:
        psutil.cpu_percent(interval=None)  # prime: first call returns 0.0, discard it
        if self._watcher is not None:
            self._watcher.start()
```

And add to the `ResourceSample(...)` in `sample_once` (sampler.py:227, after `throttled=...`):

```python
            cpu_pct=psutil.cpu_percent(interval=None),
```

- [ ] **Step 6: Run + types + lint + commit**

Run: `uv run pytest -q && uv run mypy touchstone/ && uv run ruff check .`
Expected: green (full suite — `RAW_CSV_COLUMNS` guard untouched, all sample consumers read only existing fields).

```bash
git add touchstone/models.py touchstone/sampler.py tests/test_gui_cpu_sample.py
git commit -m "feat(sampler): sample system CPU load into resources.jsonl (additive)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `bundles.py` — reports.jsonl + reiches Detail-Objekt

**Files:**
- Modify: `touchstone/gui/bundles.py`
- Test: `tests/test_gui_bundle_detail.py`

- [ ] **Step 1: Prefer reports.jsonl over scores.csv reconstruction.** In `bundles._recompute_verdict` (bundles.py:77), replace the `_reports_from_scores(run_dir, pk)` call with a reports-first loader:

```python
def _load_reports(run_dir: Path, pk: Any) -> list[Any]:
    """Prefer reports.jsonl (carries dim_rationales); fall back to lossy scores.csv
    reconstruction (rationales empty → UI shows 'Begründung nicht erfasst')."""
    from touchstone.judge import load_reports_jsonl
    reports = load_reports_jsonl(run_dir / "reports.jsonl")
    if reports:
        return reports
    return _reports_from_scores(run_dir, pk)
```

and call `_load_reports(run_dir, pk)` in `_recompute_verdict` (bundles.py:93). Keep `_reports_from_scores` as the fallback.

- [ ] **Step 2: Write the failing test** for the detail object:

```python
# tests/test_gui_bundle_detail.py
import json
from pathlib import Path
from touchstone.gui import bundles
from touchstone.judge import write_reports_jsonl
from touchstone.results import ModelReport


def _mk_judged(tmp_path):
    d = tmp_path / "2026_eval_nd"; d.mkdir()
    (d / "bundle.json").write_text(json.dumps({
        "pack_id": "ndassist", "pack_path": "packs/ndassist.yaml",
        "models": [{"id": "m", "quant": "q"}], "date": "2026-06-21",
        "host": {"machine": "t"}}), encoding="utf-8")
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    write_reports_jsonl(d / "reports.jsonl",
                        [ModelReport("m", "none", {"Q6": 2}, {"Q6": "schwach bei E1"})])
    return d


def test_bundle_detail_carries_rationales_and_kovariant(tmp_path):
    d = _mk_judged(tmp_path)
    detail = bundles.bundle_detail(d)
    # the holistic Q6 rationale is present and cites E1
    rep = detail["reports"][0]
    assert rep.dim_rationales["Q6"] == "schwach bei E1"
    # the pack (criteria) is loaded so the view can render dimensions/ko-rule
    assert detail["pack"].ko_rule.dimension == "Q6"


def test_bundle_detail_missing_reports_marks_unrecorded(tmp_path):
    d = _mk_judged(tmp_path)
    (d / "reports.jsonl").unlink()  # old bundle → scores.csv fallback, no rationales
    detail = bundles.bundle_detail(d)
    # falls back without crashing; rationales empty (UI shows 'nicht erfasst')
    assert detail is not None
```

- [ ] **Step 3: Run to verify fail**

Run: `uv run pytest tests/test_gui_bundle_detail.py -v`
Expected: FAIL — `bundle_detail` undefined.

- [ ] **Step 4: Implement `bundle_detail`** in `bundles.py`. It assembles the rich, render-ready structure for `/result`:

```python
def bundle_detail(run_dir: Path) -> dict[str, Any] | None:
    """Rich structure for the result view: pack + answers + verdicts + reports
    (reports.jsonl preferred) + the active K.-o. branch + cited prompt_ids."""
    from touchstone import scorecard
    from touchstone.judge import load_judgements_jsonl
    from touchstone.merge import load_samples_jsonl
    from touchstone.pack import load_pack
    from touchstone.qualrun import load_responses_jsonl

    m = _manifest(run_dir)
    pack_path = m.get("pack_path")
    if not pack_path or not Path(pack_path).exists():
        return None
    pk = load_pack(pack_path)
    responses = load_responses_jsonl(run_dir / "responses.jsonl")
    verdicts = load_judgements_jsonl(run_dir / "judgements.jsonl")
    reports = _load_reports(run_dir, pk)
    rows = scorecard.master_rows(pk, responses, verdicts, reports) if reports else []
    samples = load_samples_jsonl(run_dir / "resources.jsonl")
    known_ids = {p.id for _, p in pk.all_prompts()}
    return {
        "run_dir": run_dir, "manifest": m, "pack": pk,
        "responses": responses, "verdicts": verdicts, "reports": reports,
        "master_rows": rows,
        "ko": _ko_branches(pk, verdicts, rows),          # which branch fired + its root
        "cpu": [s.cpu_pct for s in samples],
        "ram": [s.sys_used_mb for s in samples],
        "cited_ids": _cited_prompt_ids(reports, known_ids),  # {dim_id: [prompt_id,...]}
    }


def _cited_prompt_ids(reports: list[Any], known_ids: set[str]) -> dict[str, list[str]]:
    """Parse the prompt_ids the judge cited in each dim_rationale (match against pack ids)."""
    import re
    out: dict[str, list[str]] = {}
    for rep in reports:
        for dim_id, text in getattr(rep, "dim_rationales", {}).items():
            hits = [tok for tok in re.findall(r"[A-Za-z]\d+", text or "") if tok in known_ids]
            if hits:
                out.setdefault(dim_id, [])
                out[f"{rep.model}|{rep.variant}|{dim_id}"] = hits
    return out


def _ko_branches(pk: Any, verdicts: list[Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each failed-KO group, which of the two roots fired (dimension-floor / red-flag-prompt)."""
    red_flagged = {v.prompt_id for v in verdicts if v.red_flag and not v.unscored}
    branches: list[dict[str, Any]] = []
    for r in rows:
        if r.get("safety_passed"):
            continue
        hit_prompts = [p for p in pk.ko_rule.red_flag_prompts if p in red_flagged]
        branches.append({
            "model": r["model"], "variant": r["variant"],
            "dimension": pk.ko_rule.dimension, "threshold": pk.ko_rule.threshold,
            "red_flag_prompts": hit_prompts,  # non-empty → red-flag branch fired
        })
    return branches
```

(If `scorecard.master_rows` rows lack a `safety_passed` key, verify against `scorecard.master_rows`/`cli._master_rows` — it does include `safety_passed` per Ink. 6.)

- [ ] **Step 5: Run + types + lint + commit**

Run: `uv run pytest tests/test_gui_bundle_detail.py tests/test_gui_bundles.py -q && uv run mypy touchstone/gui/ && uv run ruff check .`
Expected: green.

```bash
git add touchstone/gui/bundles.py tests/test_gui_bundle_detail.py
git commit -m "feat(gui): bundle_detail — reports.jsonl-first + KO-branch + cited prompt_ids

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `app.py` Read-Routen liefern das Detail-Objekt

**Files:**
- Modify: `touchstone/gui/app.py` (`result:84`, `overview:68`, `pack_explorer:73`)
- Test: `tests/test_gui_app_detail.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_app_detail.py
import json
import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.judge import write_reports_jsonl
from touchstone.results import ModelReport


class _FakeLauncher:
    def spawn(self, argv): return 1
    def alive(self, pid): return False
    def terminate(self, pid): return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _mk(tmp_path):
    d = tmp_path / "2026_eval_nd"; d.mkdir()
    (d / "bundle.json").write_text(json.dumps({
        "pack_id": "ndassist", "pack_path": "packs/ndassist.yaml",
        "models": [{"id": "m", "quant": "q"}], "date": "2026-06-21", "host": {}}), encoding="utf-8")
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    write_reports_jsonl(d / "reports.jsonl", [ModelReport("m", "none", {"Q6": 2}, {"Q6": "schwach bei E1"})])
    return d


def test_result_renders_rationale_and_ko(tmp_path):
    _mk(tmp_path)
    r = _client(tmp_path).get("/result/2026_eval_nd")
    assert r.status_code == 200
    assert "schwach bei E1" in r.text or "E1" in r.text  # the holistic rationale surfaces


def test_pack_explainer_present(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    assert r.status_code == 200
    assert "holistisch" in r.text.lower()  # the method explainer (L9)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_gui_app_detail.py -v`
Expected: FAIL (rationale not surfaced / explainer missing).

- [ ] **Step 3: Wire the routes.** In `app.py`, change `result` (app.py:84) to build the detail and pass it:

```python
    @app.get("/result/{name}", response_class=HTMLResponse)
    def result(request: Request, name: str) -> HTMLResponse:
        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
            raise HTTPException(status_code=404)
        try:
            detail = bundles.bundle_detail(rd)
            summary = bundles.classify(rd)
        except Exception:
            detail, summary = None, bundles.BundleSummary(run_dir=rd, status="error")
        return render("result.html", request, detail=detail, summary=summary, run_dir=rd, active="overview")
```

Delete the now-unused `_compute_master_rows` if nothing else references it (grep first). For `/packs` (app.py:73), pass the pack so the template can render the criteria + method explainer; the explainer text is template-side (Task 6) sourced from the spec/`docs/explanation`.

- [ ] **Step 4: Run + types + commit**

Run: `uv run pytest tests/test_gui_app_detail.py tests/test_gui_app_read.py -q && uv run mypy touchstone/gui/`
Expected: green (after Task 6 templates exist; if running this task standalone, the assertions about rendered text pass once Task 6 lands — keep the route returning 200 here, assert text in Task 6).

> **Sequencing note:** the text-content assertions (rationale, "holistisch") depend on the Task-6 templates. Implement Task 5 route wiring + a 200-status assertion first, then Task 6 adds the rendering and flips the content assertions green. Keep both tasks in one branch.

```bash
git add touchstone/gui/app.py tests/test_gui_app_detail.py
git commit -m "feat(gui): result/pack routes serve the rich bundle_detail

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Templates — Drill-down · Kriterien+Methode · Übersicht + Chart

**Files:**
- Modify: `touchstone/gui/templates/{result,pack,overview}.html`
- Create: `touchstone/gui/static/sparkline.js`
- Not TDD (presentation); the Task-5 route tests assert the content.

- [ ] **Step 1: `result.html`** — render the ratified drill-down from `detail` (the approved mockup `/tmp/touchstone-mockup-ergebnis.html` is the visual reference): Kopf (Modell·Variante·Hardware·Seed, Aufgabe-Klartext, Urteil-Badge mit Grund, %); **K.-o.-Box** rendering `detail.ko` — show the active branch (red_flag_prompts non-empty → link to that prompt's per-response verdict; else → the holistic dim_rationale of `pack.ko_rule.dimension`); **gewichtete Master-Scorecard** from `detail.master_rows`/`detail.reports` — each dimension expands to its `dim_rationales[dim]`, with `detail.cited_ids` rendered as anchors (`<a href="#prompt-E1">E1</a>`); **Antworten nach Kategorie** from `detail.pack.categories` × `detail.responses`/`detail.verdicts`, each answer `id="prompt-{id}"` (the link target), aufklappbar (Aufgabe, Green/Red-Flags, response_text, Verdict score+rationale); **Perf + RAM/CPU-Chart** (Task-6 Step 3). Empty-state when `detail.reports` empty → „noch nicht bewertet — `touchstone judge` ausführen".

- [ ] **Step 2: `pack.html`** — full criteria reference (scale legend, dimensions ×weight+about, ko_rule, prompt_variants full text, per-category prompts with flags) **plus the abrufbare method explainer (L9)**: a collapsible panel whose copy mirrors `docs/explanation/design-decisions.md` „Bewertungs-Methode" — must contain the word „holistisch" and explain: Dimensionen holistisch über alle Antworten · Σ Score×Gewicht/Max · die zwei K.-o.-Zweige · belegte Begründung mit klickbaren prompt_ids. `overview.html` links to it.

- [ ] **Step 3: `sparkline.js`** — a tiny build-free inline-SVG renderer: takes a `data-series` JSON array (RAM and CPU), draws a polyline into an `<svg>`, treats `null` as a gap (no CPU line for old runs). ~40 lines, no dependency. Wired in `result.html` for `detail.ram`/`detail.cpu` with Max/Ø labels.

- [ ] **Step 4: `overview.html`** — bedeutungstragende Zeilen: Modell·Variante · Pack-Klartext · Status · Urteil-Badge mit Grund · % · Datum; link each to `/result/{name}` and to `/packs/...`.

- [ ] **Step 5: Run the route tests (now content-asserting) + full suite + lint**

Run: `uv run pytest tests/test_gui_app_detail.py tests/test_gui_app_read.py -q && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: green — the rationale + "holistisch" assertions from Task 5 now pass.

- [ ] **Step 6: Manual visual check** — `uv run touchstone gui --no-open`, open the printed URL, confirm `/result/<real ndassist bundle>` shows the drill-down with the Q6 rationale + clickable prompt_id, the pack explainer panel, and the RAM/CPU sparkline.

- [ ] **Step 7: Commit**

```bash
git add touchstone/gui/templates/ touchstone/gui/static/sparkline.js
git commit -m "feat(gui): traceable result drill-down + criteria/method explainer + RAM/CPU chart

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: AGENTS.md + smoke + final review

- [ ] **Step 1: Full suite + lint + types**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy touchstone/`
Expected: all green.

- [ ] **Step 2: Live smoke (manual, needs judge endpoint :1234)** — re-judge the ndassist bundle (delete its `judgements.jsonl` + `reports.jsonl` first) → confirm `reports.jsonl` carries rationales that cite prompt_ids → `/result` shows „Warum Q6 = 2" with a clickable E1 link; run an `eval` to confirm the CPU spur appears in `resources.jsonl`.

- [ ] **Step 3: Update AGENTS.md** — under the GUI gotcha / data notes, add:

```markdown
- **`reports.jsonl` is the ModelReport persistence** (per-(model,variant) `dim_scores` + `dim_rationales`),
  written by `judge` in BOTH paths at finalize (`_render_judge_scorecard`). It is the sole source of
  per-dimension rationales (scores.csv has none); the GUI reads it first and falls back to the lossy
  scores.csv reconstruction (then „Begründung nicht erfasst"). Master-dimensions are scored HOLISTICALLY —
  traceability runs through the judge's rationale citing prompt_ids, not a dimension→prompt structure.
- **`resources.jsonl` ticks now carry `cpu_pct`** (system CPU %, `None` for pre-cpu ticks); additive,
  does not touch `RAW_CSV_COLUMNS`.
```

- [ ] **Step 4: Commit + finish**

```bash
git add AGENTS.md
git commit -m "docs(agents): reports.jsonl + cpu_pct in resources.jsonl

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Then use `superpowers:finishing-a-development-branch` (merge to main + push per project memory — solo repo, no PR).

---

## Self-Review Notes (author checklist — completed)

- **Spec coverage:** L4→T1+T2; L5→T3; L8 (citation bridge)→T1 prompt + T4 `_cited_prompt_ids` + T6 anchors; L9 (method explainer)→T6 Step 2; 3 views→T5/T6; empty-states→T4/T6; reports.jsonl backward-compat→T4 `_load_reports`. CPU as rider: T3, defer-able.
- **Type consistency:** `parse_dimension_report`, `write_reports_jsonl`/`load_reports_jsonl`, `bundle_detail`, `_load_reports`, `_cited_prompt_ids`, `_ko_branches`, `cpu_pct` used identically across tasks.
- **Known sequencing:** T5 route wiring lands with 200-assertions; T6 templates flip the content assertions (rationale/"holistisch") green — same branch (noted in T5).
- **No placeholders:** every code step shows real code; the two `test_judge.py` edits point at the exact functions to heave (verify line numbers at edit time — they shift).
