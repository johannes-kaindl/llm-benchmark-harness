# GUI Best-Practices Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the raw `ramcheck gui` control-center into a self-explaining, feature-complete UI — without switching frameworks — by adding a component/glossary spine, rendering already-captured data, fixing one real bug, and adding two small instrumentation features.

**Architecture:** Additive on the existing out-of-process FastAPI + Jinja2 + Alpine.js stack. Two new substrates (a Jinja macro library and a pure `glossary.py`) make the UI self-explaining; every other change renders data that already lives in `responses.jsonl`/`scores.csv`, adds a read-only viewer route, or adds one cheap capture (RAM baseline, reasoning timing). `runs/` stays SSOT; pure logic is unit-tested, I/O is dependency-injected.

**Tech Stack:** Python 3.12 · uv · FastAPI/Jinja2/Alpine (GUI `[gui]` extra) · pydantic · pytest · ruff · mypy strict.

**Reference:** spec `docs/superpowers/specs/2026-06-23-gui-best-practices-redesign-design.md`.

> **STATUS (2026-06-24):** Phasen **P1–P7 vollständig implementiert und gemergt** (Merge `07ade7d`, je ein Feature-Commit pro Phase, inkl. Pre-Merge-Review). Der nachgelagerte Markdown-Report-Export ist ebenfalls gemergt. **Offen:** P0 (Tool-Rename — blockiert auf Ziel-Name) und der **Final-E2E-Smoke** gegen echte Hardware (Zeile 675). 429 Tests grün.

**Conventions (apply to every task):**
- Run tests with `uv run pytest <path> -q`; lint `uv run ruff check . && uv run ruff format .`; types `uv run mypy ramcheck/`.
- GUI tests use `_client(tmp_path)` → `TestClient(gui_app.create_app(runs_dir=tmp_path, registry=RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())))`; bundle fixtures via `_write_bundle` / `_write_compare_bundle` / `_mk_judged` (copy the local helper from the sibling test file).
- **After any template/route/static change:** restart the server and verify the touched route headless (`uv run ramcheck gui &` then `curl -s localhost:PORT/route | head`); a stale process serves dead routes.
- Commit after every green task. Branch `feat/gui-best-practices-redesign`.

---

## Phase P1 — The bug: hardware-label contradiction

**Files:**
- Create: `ramcheck/gui/hwlabel.py` (pure mismatch detector)
- Modify: `ramcheck/gui/compare.py` (cross-run rows carry a mismatch flag), `ramcheck/gui/templates/compare.html:12-50`
- Test: `tests/test_gui_hwlabel.py`

**Context:** `compare.html` renders `r.chip` (live `hostinfo`), `r.ram_gb` (live), `r.machine` (static `Config.machine` YAML label). On a reused config the label goes stale → "M5 Pro / 64 GB / M1-16GB". `AggRow` (`aggregate.py:40-57`) already has `chip`, `ram_gb`, `machine`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_gui_hwlabel.py
from ramcheck.gui.hwlabel import label_mismatch


def test_label_matches_detected_hardware_is_not_flagged():
    assert label_mismatch(chip="Apple M5 Pro", ram_gb="64.0", machine="M5-Pro-64GB") is False


def test_stale_label_contradicting_detected_chip_is_flagged():
    # label says M1/16GB but the run actually executed on an M5 with 64GB
    assert label_mismatch(chip="Apple M5 Pro", ram_gb="64.0", machine="M1-16GB") is True


def test_empty_or_unknown_label_is_not_flagged():
    assert label_mismatch(chip="Apple M5 Pro", ram_gb="64.0", machine="") is False
    assert label_mismatch(chip="", ram_gb="", machine="whatever") is False
```

- [x] **Step 2: Run it, verify it fails**

Run: `uv run pytest tests/test_gui_hwlabel.py -q` → FAIL (module not found).

- [x] **Step 3: Implement `hwlabel.py`**

```python
# ramcheck/gui/hwlabel.py
"""Detect a stale Config.machine label that contradicts the detected hardware.

chip/ram_gb come from live hostinfo at eval time; machine is a hand-typed YAML
label. Reusing a config on new hardware without editing the label produces a row
where the detected truth and the label disagree. We never trust the label over
detected hardware — we flag the disagreement so the UI can demote it.
"""

from __future__ import annotations

import re

_GEN = re.compile(r"\bM(\d+)\b", re.IGNORECASE)  # M1, M2, ... chip generation


def label_mismatch(*, chip: str, ram_gb: str, machine: str) -> bool:
    """True when the machine label contradicts detected chip/ram (best-effort).

    Conservative: only flags a *positive* contradiction (label names a chip
    generation or RAM size that differs from detected). Empty/unknown values
    never flag — absence of a label is not a contradiction.
    """
    if not machine or not chip:
        return False
    det_gen = _GEN.search(chip)
    lab_gen = _GEN.search(machine)
    if det_gen and lab_gen and det_gen.group(1) != lab_gen.group(1):
        return True
    lab_ram = re.search(r"(\d+)\s*GB", machine, re.IGNORECASE)
    if lab_ram and ram_gb:
        try:
            if abs(float(lab_ram.group(1)) - float(ram_gb)) >= 1.0:
                return True
        except ValueError:
            return False
    return False
```

- [x] **Step 4: Run test, verify pass**

Run: `uv run pytest tests/test_gui_hwlabel.py -q` → PASS.

- [x] **Step 5: Wire the flag into the cross-run rows**

In `ramcheck/gui/compare.py`, after `aggregate()` produces `AggRow`s the route hands them to the template. The simplest non-invasive wiring: compute the flag in the template via a tiny exposed helper. Add to `compare.py` (or wherever `compare_cross` builds context) a post-process that attaches `mismatch` per row. Concretely, change the `/compare` route in `ramcheck/gui/app.py:114-118` to:

```python
    @app.get("/compare", response_class=HTMLResponse)
    def compare_cross(request: Request) -> HTMLResponse:
        from ramcheck.gui.hwlabel import label_mismatch

        rows = aggregate_mod.load_all_scores(runs_dir)
        agg = aggregate_mod.aggregate(rows) if rows else []
        flagged = [
            (a, label_mismatch(chip=a.chip, ram_gb=str(a.ram_gb), machine=a.machine))
            for a in agg
        ]
        return render("compare.html", request, rows=flagged, active="compare")
```

- [x] **Step 6: Update the template to demote a stale label**

In `ramcheck/gui/templates/compare.html`, change the loop from `{% for r in rows %}` over `AggRow` to unpack the tuple, and render the `Maschine` cell muted with a warning when flagged. Replace lines 27-50 region's `<tr>...<td>{{ r.machine }}</td>...` with:

```html
      {% for r, mismatch in rows %}
      <tr>
        <td>{{ r.chip }}</td>
        <td>{{ r.ram_gb }}</td>
        <td>
          {% if mismatch %}
            <span class="muted" title="Config-Label »{{ r.machine }}« passt nicht zur erkannten Hardware – erkannte Werte gelten.">{{ r.machine }} ⚠</span>
          {% else %}
            {{ r.machine }}
          {% endif %}
        </td>
        <td>{{ r.pack }}</td>
        <td>{{ r.model }}</td>
        <td>{{ r.variant }}</td>
        <td>{{ r.quant }}</td>
        <td>
          {% if r.quality_pct is not none %}<strong>{{ "%.0f" | format(r.quality_pct) }}%</strong>{% else %}<span class="muted">—</span>{% endif %}
        </td>
        <td>{{ r.ttft_p50 }}</td>
        <td>{{ r.decode_med }}</td>
        <td>{{ r.peak_ram_gb }}</td>
      </tr>
      {% endfor %}
```

- [x] **Step 7: Add a route smoke test**

```python
# append to tests/test_gui_hwlabel.py — reuse a _write_bundle helper copied from tests/test_gui_bundles.py
# Build a bundle whose scores.csv carries chip "Apple M5 Pro"/ram "64.0" but machine "M1-16GB",
# GET /compare, assert "⚠" in r.text and status 200.
```

(Copy `_write_bundle` + `_client` from `tests/test_gui_bundles.py`; the scores.csv header is `model,variant,metric_type,metric,weight,score` plus the hardware columns written by `scorecard.scores_csv_rows` — assert the warning glyph renders.)

- [x] **Step 8: Verify headless + commit**

```bash
uv run pytest tests/test_gui_hwlabel.py -q && uv run ruff check . && uv run mypy ramcheck/gui/hwlabel.py
git add ramcheck/gui/hwlabel.py ramcheck/gui/app.py ramcheck/gui/compare.py ramcheck/gui/templates/compare.html tests/test_gui_hwlabel.py
git commit -m "fix(gui): demote stale machine label that contradicts detected hardware (P1)"
```

---

## Phase P2a — Spine: metric glossary

**Files:**
- Create: `ramcheck/gui/glossary.py`, `tests/test_gui_glossary.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_gui_glossary.py
from ramcheck.gui.glossary import GLOSSARY, describe


def test_every_entry_has_term_short_long():
    for key, g in GLOSSARY.items():
        assert g.term and g.short and g.long, f"incomplete glossary entry: {key}"


def test_describe_known_key_returns_short():
    assert "Token" in describe("ttft_p50").short or "TTFT" in describe("ttft_p50").term


def test_describe_unknown_key_returns_safe_placeholder():
    d = describe("does_not_exist")
    assert d.term == "does_not_exist"
    assert d.short == ""
```

- [x] **Step 2: Run, verify fail.** `uv run pytest tests/test_gui_glossary.py -q`

- [x] **Step 3: Implement `glossary.py`**

```python
# ramcheck/gui/glossary.py
"""Single source of truth for what every metric/label in the GUI means.

Pure data — no GUI imports — so it is unit-testable and so a render-time test can
assert that every metric key a template shows is defined here (no unexplained
number can ship). The `metric()` Jinja macro pulls `short` for tooltips.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Glossary:
    term: str  # human label
    short: str  # one-line tooltip
    long: str  # full explanation (glossary page / explainer)


GLOSSARY: dict[str, Glossary] = {
    "quality_pct": Glossary(
        "Qualität %",
        "Σ(Score × Gewicht) / Max × 100 über die holistisch bewerteten Dimensionen.",
        "Der Judge vergibt pro Master-Dimension 1–5; gewichtet, normiert auf 0–100 %. "
        "Belegt durch eine Begründung mit zitierten Prompt-IDs.",
    ),
    "ttft_p50": Glossary(
        "TTFT P50",
        "Time-To-First-Token, Median (s): Wartezeit bis zum ersten sichtbaren Antwort-Token.",
        "Reasoning-Tokens zählen NICHT zur TTFT — erst der erste Content-Token stoppt die Uhr.",
    ),
    "decode_median": Glossary(
        "Decode Median",
        "Median der Decode-Geschwindigkeit (Token/s) im Antwort-Fenster nach der TTFT.",
        "completion_tokens / (e2e − ttft). Reine Generier-Rate, ohne Prefill.",
    ),
    "prefill_tps": Glossary(
        "Prefill",
        "Prompt-Verarbeitung (Token/s): prompt_tokens / TTFT.",
        "Wie schnell der Prompt eingelesen wird, bevor das erste Token kommt.",
    ),
    "e2e": Glossary(
        "Gesamtzeit",
        "End-to-End-Wandzeit einer Antwort (s), von Absenden bis letztem Token.",
        "Enthält Prefill + Thinking + Decode.",
    ),
    "total_throughput": Glossary(
        "Gesamt-Durchsatz",
        "(prompt_tokens + completion_tokens) / Gesamtzeit (Token/s).",
        "Effektiver Durchsatz inkl. Prompt-Verarbeitung — näher am gefühlten Tempo als Decode allein.",
    ),
    "system_peak_ram": Glossary(
        "System-Peak",
        "Höchster System-Speicher während des Laufs (GB) — OS + alle Prozesse, nicht nur das Modell.",
        "Auf Apple-Silicon mmap't mlx die Gewichte in den Unified Memory; RSS unterzählt. "
        "Diese Zahl ist NICHT maschinen-vergleichbar — siehe Modell-Delta.",
    ),
    "model_delta_ram": Glossary(
        "Modell-Delta",
        "System-Peak minus Baseline vor dem Lauf (GB) — der maschinen-vergleichbare Speicher-Zuwachs.",
        "Baseline = Systemspeicher unmittelbar vor der ersten Anfrage. Der Kontext-/KV-Anteil "
        "ist im Unified Memory nicht sauber von den Gewichten trennbar (dokumentierte Unschärfe).",
    ),
    "mem_pressure": Glossary(
        "Memory-Pressure",
        "macOS-Speicherdruck (normal/warn/critical) im Lauf-Fenster.",
        "Aus `memory_pressure`. Steigt, wenn das System komprimiert/auslagert.",
    ),
    "variant_baseline": Glossary(
        "baseline",
        "Lauf MIT dem im Pack definierten System-Prompt.",
        "Die »baseline«-Variante setzt den Einsatzzweck-Prompt; Vergleich gegen »none« misst dessen Effekt.",
    ),
    "variant_none": Glossary(
        "none",
        "Lauf OHNE System-Prompt (Kontrollgruppe).",
        "Misst das nackte Modell; Differenz zu »baseline« = Wirkung des Prompts.",
    ),
    "reasoning_only": Glossary(
        "reasoning-only",
        "Modell schrieb nur ins Reasoning-Feld, keine sichtbare Antwort → aus dem Mittel ausgenommen.",
        "Setup-Hinweis (z. B. Token-Budget zu klein), kein inhaltliches Urteil. Echt leer bleibt 1.",
    ),
    "reasoning_duration": Glossary(
        "Thinking-Dauer",
        "Zeit im Reasoning-Kanal (s), bevor der erste Content-Token kommt.",
        "Getrennt von der Antwortzeit gemessen.",
    ),
    "reasoning_tps": Glossary(
        "Thinking-Tempo",
        "Reasoning-Token/s (heuristisch gezählt).",
        "Die API liefert nur eine aggregierte completion_tokens-Zahl; Reasoning-Tokens werden geschätzt.",
    ),
    "cold_start": Glossary(
        "Cold-Start",
        "Allererste Anfrage des Laufs — separat ausgewiesen, nie in den Aggregaten.",
        "Misst »wird das Modell warm«; aus Mittelwerten ausgeschlossen.",
    ),
    "cpu": Glossary(
        "CPU Ø/Max",
        "System-CPU-Last (%) im Antwort-Fenster, Durchschnitt/Maximum.",
        "»n. v.«, wenn das Bundle vor der CPU-Erfassung lief.",
    ),
}


def describe(key: str) -> Glossary:
    """Glossary entry for a key, or a safe placeholder for an unknown key."""
    return GLOSSARY.get(key, Glossary(term=key, short="", long=""))
```

- [x] **Step 4: Run, verify pass.** `uv run pytest tests/test_gui_glossary.py -q` → PASS.

- [x] **Step 5: Commit.**

```bash
uv run ruff check . && uv run mypy ramcheck/gui/glossary.py
git add ramcheck/gui/glossary.py tests/test_gui_glossary.py
git commit -m "feat(gui): metric glossary — single source of truth for UI explanations (P2a)"
```

---

## Phase P2b — Spine: Jinja macro library + glossary injection

**Files:**
- Create: `ramcheck/gui/templates/macros/ui.html`
- Modify: `ramcheck/gui/app.py:42-68` (inject glossary into the Jinja env globals), `ramcheck/gui/templates/base.html`
- Test: `tests/test_gui_macros.py`

- [x] **Step 1: Make the glossary available to all templates.** In `ramcheck/gui/app.py`, where `_templates` (Jinja2Templates) is defined, add the glossary as a global so macros can call it. Add near the module-level `_templates` definition:

```python
from ramcheck.gui import glossary as _glossary

_templates.env.globals["g"] = _glossary.describe  # g("ttft_p50").short in templates
```

- [x] **Step 2: Write the macro library.**

```html
{# ramcheck/gui/templates/macros/ui.html — reusable presentation macros #}
{% macro metric(value, key, unit="") -%}
  {%- set gd = g(key) -%}
  <span class="metric" title="{{ gd.short }}">{{ value }}{% if unit %} {{ unit }}{% endif %}{% if gd.short %}<span class="metric-help" aria-hidden="true">ⓘ</span>{% endif %}</span>
{%- endmacro %}

{% macro mlabel(key) -%}
  {%- set gd = g(key) -%}
  <span class="metric-label" title="{{ gd.short }}">{{ gd.term }}{% if gd.short %}<span class="metric-help" aria-hidden="true">ⓘ</span>{% endif %}</span>
{%- endmacro %}

{% macro badge(text, kind) -%}
  <span class="badge {{ kind }}">{{ text }}</span>
{%- endmacro %}

{% macro alert(kind, body) -%}
  <div class="alert alert-{{ kind }}">{{ body }}</div>
{%- endmacro %}

{% macro kv(label, value) -%}
  <div class="kv-row"><span class="kv-label">{{ label }}</span><span class="kv-value">{{ value }}</span></div>
{%- endmacro %}
```

- [x] **Step 2b: Add minimal CSS** for `.metric-help`, `.metric-label`, `.kv-row`, `.kv-label`, `.kv-value`, `.alert` in `ramcheck/gui/static/app.css` (mirror existing token style; `.metric-help{font-size:0.7em;opacity:0.5;margin-left:2px}`; `.kv-row{display:flex;gap:1rem;padding:0.15rem 0;border-bottom:1px solid var(--border)}` etc.).

- [x] **Step 3: Write the test (glossary completeness gate + macro render).**

```python
# tests/test_gui_macros.py
from fastapi.testclient import TestClient
from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunRegistry
# copy _FakeLauncher from tests/test_gui_app_read.py


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def test_glossary_global_is_registered():
    # the macro layer relies on g(key) being a Jinja global
    assert "g" in gui_app._templates.env.globals
```

- [x] **Step 4: Run, verify pass.** Then commit.

```bash
uv run pytest tests/test_gui_macros.py -q && uv run ruff check .
git add ramcheck/gui/templates/macros/ui.html ramcheck/gui/app.py ramcheck/gui/static/app.css tests/test_gui_macros.py
git commit -m "feat(gui): Jinja macro library + glossary injected as template global (P2b)"
```

---

## Phase P2c — Explain layer + drop HTMX dead weight

**Files:** Modify `ramcheck/gui/templates/compare.html`, `compare_axis.html`, `config.html`, `pack.html`, `_method_explainer.html`, `base.html`; `pyproject.toml`; delete `ramcheck/gui/static/htmx.min.js`.

- [x] **Step 1: Metric headers via `mlabel`.** In `compare.html` `<thead>` replace bare `<th>Qualität %</th><th>TTFT P50</th><th>Decode Median</th><th>Peak RAM</th>` with `{% import "macros/ui.html" as ui %}` at top and `<th>{{ ui.mlabel("quality_pct") }}</th>` etc. Do the same for the row labels in `compare_axis.html` (`Qualität`, `Decode`, `TTFT P50`, `e2e Median`, `Peak-RAM`, `CPU Ø/Max` → `ui.mlabel("quality_pct")`, `"decode_median"`, `"ttft_p50"`, `"e2e"`, `"system_peak_ram"`, `"cpu"`).

- [x] **Step 2: Model-checkbox help + variant legend in `config.html`.** Add under the `Modelle` label: `<p class="muted text-xs">Nur angehakte Modelle werden evaluiert. Abwählen schließt ein Modell aus diesem Lauf aus (nichts wird in die Config geschrieben).</p>`.

- [x] **Step 3: Variant legend in `compare_axis.html` + `pack.html`.** Where variants `baseline`/`none` appear, add a one-line legend pulling `g("variant_baseline").short` / `g("variant_none").short`.

- [x] **Step 4: Pack-name hyperlink in cross-run compare.** In `compare.html` change `<td>{{ r.pack }}</td>` to `<td><a href="/packs/packs/{{ r.pack }}.yaml">{{ r.pack }}</a></td>`.

- [x] **Step 5: Breadcrumbs.** Add a `{% block breadcrumb %}{% endblock %}` to `base.html` above the main content; set it in `result.html`/`compare_axis.html`/`pack.html` (e.g. `Übersicht › Ergebnis › {{ run_dir.name }}`).

- [x] **Step 6: Reformat `_method_explainer.html`.** Wrap the weighting formula in `<pre class="formula">Σ (Score × Gewicht) / Max × 100 = Qualität %</pre>`; render the two K.-o. branches as two `<div class="card ko-branch">` cards; render the 1–5 scale as a small `<table>`.

- [x] **Step 7: Drop HTMX.** Remove the `<script src="/static/htmx.min.js">` tag from `base.html`; delete `ramcheck/gui/static/htmx.min.js`. (HTMX is not in `pyproject.toml` deps — it is a vendored asset only — so only the file + tag are removed.)

- [x] **Step 8: Smoke test the explain layer.**

```python
# tests/test_gui_explain.py — copy _client/_FakeLauncher/_write_bundle
def test_compare_headers_have_glossary_tooltips(tmp_path):
    _write_bundle(tmp_path / "2026_eval_nd", groups=[("m", "baseline")], scores_by_group={("m", "baseline"): {"Q1": 4}})
    r = _client(tmp_path).get("/compare")
    assert r.status_code == 200
    assert "Time-To-First-Token" in r.text  # tooltip text from glossary

def test_base_no_longer_references_htmx(tmp_path):
    r = _client(tmp_path).get("/")
    assert "htmx.min.js" not in r.text
```

- [x] **Step 9: Restart server, headless-verify `/`, `/compare`, `/config`, `/packs/packs/ndassist.yaml`; commit.**

```bash
git add -A ramcheck/gui pyproject.toml tests/test_gui_explain.py
git rm ramcheck/gui/static/htmx.min.js
git commit -m "feat(gui): self-explaining layer — metric tooltips, legends, breadcrumbs, reformatted explainer; drop unused HTMX (P2c)"
```

---

## Phase P3 — Result perf panel + full text + e2e_med

**Files:** Modify `ramcheck/gui/templates/result.html:318-356`, `ramcheck/gui/templates/pack.html:39-78`, `ramcheck/gui/bundles.py:164-204`, `ramcheck/scorecard.py:101-113` + `:296-309`, `ramcheck/aggregate.py`; tests `tests/test_scorecard.py`/`test_scorecard_render.py`, `tests/test_gui_result_perf.py`.

### Task P3.1 — Backend rider: `e2e_med` into the perf summary + scores.csv

- [x] **Step 1: Failing test** in `tests/test_scorecard_render.py`-style (pure):

```python
# tests/test_scorecard_e2e.py
from ramcheck.scorecard import _perf_summary
# copy _resp() builder from tests/test_scorecard_render.py (EvalResponse with e2e_s set)

def test_perf_summary_includes_e2e_med():
    resps = [_resp(e2e_s=1.0, ok=True), _resp(e2e_s=3.0, ok=True)]
    p = _perf_summary(resps)
    assert p["e2e_med"] == 2.0
```

- [x] **Step 2: Run, verify fail.**

- [x] **Step 3: Add `e2e_med` to `_perf_summary`** (`scorecard.py:101-113`). After `decodes = [...]` add `e2es = [r.e2e_s for r in ok if not math.isnan(r.e2e_s)]`; in the returned dict add `"e2e_med": median(e2es),`.

- [x] **Step 4: Add `e2e_med` to the scores.csv base dict** (`scorecard.py:296-309`): after `"decode_med": _num(p["decode_med"]),` add `"e2e_med": _num(p["e2e_med"]),`.

- [x] **Step 5: Carry `e2e_med` through aggregate.** In `aggregate.py` add `e2e_med: str` to `AggRow` (after `decode_med`) and read it in the row builder; ensure `write_scores_all_csv` carries the column. Add to `tests/test_aggregate.py` `HEADER` the `e2e_med` column.

- [x] **Step 6: Run scorecard+aggregate tests, verify pass; commit.**

```bash
uv run pytest tests/test_scorecard_e2e.py tests/test_aggregate.py -q
git commit -am "feat(scorecard): persist e2e_med so cross-run total-throughput survives the aggregate path (P3.1)"
```

### Task P3.2 — Per-answer metric block in `result.html`

- [x] **Step 1:** `bundle_detail` already passes `detail["responses"]` (list of `EvalResponse`) with `ttft_s, decode_tps, prefill_tps, e2e_s, prompt_tokens, completion_tokens, reasoning_chars` — no backend change. In `result.html`, inside the per-answer loop (`318-356`), after the `Modell-Antwort` block insert a metrics row using the macro and a computed total throughput:

```html
{% import "macros/ui.html" as ui %}
{# ... inside the {% for resp in ns2.responses %} block, after the answer div ... #}
<div class="flex gap-2 text-xs muted" style="flex-wrap:wrap; margin-top:0.35rem">
  {% if resp.ttft_s is not none and resp.ttft_s == resp.ttft_s %}{{ ui.metric("%.2f"|format(resp.ttft_s), "ttft_p50", "s") }}{% endif %}
  {% if resp.decode_tps is not none and resp.decode_tps == resp.decode_tps %}{{ ui.metric("%.0f"|format(resp.decode_tps), "decode_median", "tok/s") }}{% endif %}
  {% if resp.prefill_tps is not none and resp.prefill_tps == resp.prefill_tps %}{{ ui.metric("%.0f"|format(resp.prefill_tps), "prefill_tps", "tok/s") }}{% endif %}
  {% if resp.e2e_s is not none %}{{ ui.metric("%.2f"|format(resp.e2e_s), "e2e", "s") }}{% endif %}
  {% if resp.e2e_s and resp.e2e_s > 0 %}{{ ui.metric("%.0f"|format((resp.prompt_tokens + resp.completion_tokens) / resp.e2e_s), "total_throughput", "tok/s") }}{% endif %}
  <span class="metric" title="Prompt-/Antwort-Tokens">{{ resp.prompt_tokens }}→{{ resp.completion_tokens }} tok</span>
  {% if resp.reasoning_chars > 0 %}<span class="metric" title="{{ g('reasoning_only').short }}">💭 {{ resp.reasoning_chars }} Z.</span>{% endif %}
</div>
```

(`x == x` is the Jinja idiom for "not NaN".)

- [x] **Step 2: Expandable reasoning block.** When `resp.reasoning_text` is non-empty (persisted only on `content_empty`), add an Alpine collapsible mirroring the answer block, showing `resp.reasoning_text` with `white-space:pre-wrap`.

- [x] **Step 3: Test.**

```python
# tests/test_gui_result_perf.py — copy _client/_FakeLauncher/_write_bundle (with perf overrides)
def test_result_shows_per_answer_perf(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _write_bundle(d, groups=[("m", "baseline")], scores_by_group={("m", "baseline"): {q: 4 for q in DIMS}},
                  perf={"ttft_s": 0.2, "decode_tps": 30.0, "e2e_s": 1.5, "prompt_tokens": 100, "completion_tokens": 50})
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert r.status_code == 200
    assert "tok/s" in r.text and "→" in r.text  # per-answer metric block rendered
```

- [x] **Step 4: Restart + headless-verify `/result/<bundle>`; commit.**

### Task P3.3 — Full prompt + full system-prompt in `pack.html`

- [x] **Step 1:** Remove the `[:80]` slice (`pack.html:43`) — render the full `pv.system_prompt` inside an Alpine collapsible (`white-space:pre-wrap`), collapsed by default.
- [x] **Step 2:** In the prompts loop (`pack.html:60-73`), after the title row add a collapsible showing `{{ p.prompt }}` (full text, `pre-wrap`) and, when present, `{{ p.tests }}` as the rubric.
- [x] **Step 3: Test** `tests/test_gui_pack_view.py`: GET `/packs/packs/ndassist.yaml`; assert a known full prompt substring (longer than 80 chars) appears in `r.text` and is not truncated with `…`.
- [x] **Step 4: Restart + headless-verify; commit.**

```bash
git commit -am "feat(gui): per-answer perf/token block + full prompt & system-prompt text (P3.2/P3.3)"
```

---

## Phase P4 — Config/Pack viewer + ephemeral overrides + YAML export

**Files:** Create `ramcheck/gui/templates/config_view.html`; modify `ramcheck/gui/app.py` (new `/config-view/{config_path}` + `/export-yaml` routes; extend `start_eval` with a small override whitelist), `ramcheck/config.py` (an `apply_overrides` whitelist fn), `config.html` (a "?" link). Tests `tests/test_gui_config_view.py`, `tests/test_config_overrides.py`.

### Task P4.1 — Read-only config viewer route

- [x] **Step 1: Test** (mirror `pack_explorer`): `GET /config-view/config.m5.yaml` → 200, body contains endpoint base_url, machine, `runs_per_cell`, a model id; `GET /config-view/../etc/passwd` → 404.
- [x] **Step 2: Route** in `app.py` mirroring `pack_explorer` (`77-87`):

```python
    @app.get("/config-view/{config_path:path}", response_class=HTMLResponse)
    def config_view(request: Request, config_path: str) -> HTMLResponse:
        candidate = Path(config_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise HTTPException(status_code=404)
        if config_path not in {str(p) for p in Path(".").glob("config*.yaml")}:
            raise HTTPException(status_code=404)
        try:
            cfg = load_config(config_path)
        except (FileNotFoundError, OSError):
            raise HTTPException(status_code=404) from None
        return render("config_view.html", request, cfg=cfg, path=config_path, active="config")
```

- [x] **Step 3: Template** `config_view.html` using `ui.kv` macros for endpoint (mask api_key), machine, runs_per_cell, seed, temperature, context_buckets, scenarios, max_tokens, server_process_match, power_check, engine/engine_version, vlm, embed, and a models table (id, quant, max_tokens_default, reasoning_headroom_tokens, extra_body). Add a "Config-Datei herunterladen" link → `/export-yaml?kind=config&path=...`.
- [x] **Step 4: "?" link** next to the Config picker in `config.html` opening `/config-view/{{ config }}` in a new tab (Alpine: `:href="'/config-view/' + config"`).
- [x] **Step 5: Run tests, restart, headless-verify, commit.**

### Task P4.2 — YAML export route (effective pack/config)

- [x] **Step 1: Test:** `GET /export-yaml?kind=config&path=config.m5.yaml` → 200, `text/yaml`, body parses via `yaml.safe_load` back into a dict with `machine`; bad kind/path → 404.
- [x] **Step 2: Route:** validate `kind in {"config","pack"}` and the path against the offered `config*.yaml`/`packs/*.yaml` globs; `load_config`/`load_pack`; serialize `yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False, allow_unicode=True)`; return as `Response(media_type="application/x-yaml", headers={"Content-Disposition": f'attachment; filename="{name}"'})`.
- [x] **Step 3: Run, restart, commit.**

### Task P4.3 — Ephemeral non-model overrides at start

- [x] **Step 1: Test** in `tests/test_config_overrides.py` (pure): `apply_overrides(cfg, {"runs_per_cell": 4, "seed": 7})` returns a copy with those replaced and everything else intact; an unknown key (`{"endpoint": ...}`) raises `ValueError`; empty dict is a no-op (returns equal config).
- [x] **Step 2: Implement** `apply_overrides(cfg: Config, overrides: dict[str, object]) -> Config` in `config.py`, whitelisting `{"runs_per_cell", "seed", "temperature"}` only, validating types, using `cfg.model_copy(update=...)` then re-validating via `Config.model_validate(updated.model_dump())` so validators run. (Models keep their existing `apply_models_override` path.)
- [x] **Step 3: DEFERRED — do not wire into the spawn this round.** Decision gate resolved: `ramcheck eval --help` confirms the CLI has **no** `--runs-per-cell`/`--seed`/`--temperature` flags (only `--resume`/`--run-dir`/`--models-json`). Therefore ship `apply_overrides` as a tested pure helper (Steps 1–2 only) and DO NOT touch `start_eval`/`RunRegistry`/`control.py` for non-model overrides. The model override path (`models_json` → `apply_models_override`) is unchanged and already wired. Note the deferral in the commit message. (Follow-up, out of scope here: add the eval CLI flags, then wire `overrides_json`.)
- [x] **Step 4: Tests green, restart, headless-verify, commit.**

```bash
git commit -am "feat(gui): read-only config viewer + YAML export + ephemeral override whitelist (P4)"
```

---

## Phase P7 — Export/import whole bundles

**Files:** Modify `ramcheck/gui/app.py` (`/export-bundle/{name}`, `/import-bundle`), `result.html` (zip button + import card or a small `/import` page). Tests `tests/test_gui_bundle_io.py`.

### Task P7.1 — Export bundle as zip

- [x] **Step 1: Test:** build a judged bundle; `GET /export-bundle/<name>` → 200, `application/zip`; open the returned bytes with `zipfile.ZipFile(io.BytesIO(r.content))`, assert it contains `bundle.json`, `responses.jsonl`, `scores.csv` and does NOT contain `run.json`/`events.jsonl`.
- [x] **Step 2: Route:**

```python
    @app.get("/export-bundle/{name}")
    def export_bundle(name: str) -> Any:
        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not (rd / "bundle.json").exists():
            raise HTTPException(status_code=404)
        ledger = ["bundle.json", "responses.jsonl", "scores.csv", "reports.jsonl",
                  "judgements.jsonl", "scorecard.md", "perf.csv", "resources.jsonl"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in ledger:
                p = rd / f
                if p.exists():
                    z.write(p, arcname=f)
        buf.seek(0)
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
```

- [x] **Step 3: Zip button** in `result.html` export card. Run, restart, commit.

### Task P7.2 — Import bundle from zip

- [x] **Step 1: Test:** zip a fixture bundle in-memory; `POST /import-bundle` (multipart file) → 200/redirect; assert a new dir landed under `runs_dir` and `bundles.classify(new_dir)` is judged. Missing `bundle.json` in zip → 400. Name collision → suffixed dir, both present.
- [x] **Step 2: Route** using `UploadFile` (python-multipart is already a `[gui]` dep): extract to a temp dir, validate `bundle.json` parses + `responses.jsonl` valid JSONL, choose a collision-safe name (`name` else `name__2`), move into `runs_dir`, return `{"run_dir": new_name}`. Apply the same Origin/CSRF guard (it's a POST — the middleware already covers it).
- [x] **Step 3: Import card** (file input → POST) on `/config` or a small `/import` page with a help card explaining portability + identity (`g`-style note: a bundle is `bundle.json`+`responses.jsonl`+`scores.csv`; `pack_path` must exist locally to view criteria).
- [x] **Step 4: Tests green, restart, headless-verify, commit.**

```bash
git commit -am "feat(gui): export/import whole bundles as zip — multi-machine aggregation (P7)"
```

---

## Phase P5 — RAM methodology: baseline-subtracted model delta

**Files:** Modify `ramcheck/sampler.py:239-251` (baseline sample), `ramcheck/merge.py` (carry baseline + delta), `ramcheck/models.py` (RAW_CSV_COLUMNS + RunRecord), `ramcheck/scorecard.py` (`_perf_summary` + scores.csv), `ramcheck/results.py` (EvalResponse `sys_used_baseline_mb`), compare/result UI labels, `AGENTS.md`, `docs/explanation/design-decisions.md`. Tests `tests/test_merge.py`, `tests/test_scorecard_*`.

> **Methodology note for the implementer:** the harness drives an *already-running* endpoint, so the model may be loaded before sampling starts. The baseline = system memory at sampler start (before the first request). On a pre-loaded server this yields the inference-time growth (KV/context/activations); on a lazy-loading server it includes weights. We report `model_delta = peak_sys_used − baseline` as the **comparable** number and label the raw peak "System-Peak". Per-prompt KV isolation is explicitly out of scope (documented).

### Task P5.1 — Capture a baseline sample

- [x] **Step 1: Test** (`tests/test_sampler_baseline.py`): a `HostSampler` whose `sample_once` is stubbed to return increasing `sys_used_mb`, run `run_to_file` for a couple ticks, assert the FIRST line written carries a `"baseline": true` marker (or that a separate `baseline_sys_used_mb` is recorded).
- [x] **Step 2: Implement.** In `sampler.py:239-251`, after `self.start()` and before the loop, take one `sample_once()` and write it first with a `baseline` flag (add `baseline: bool = False` to `ResourceSample` in `models.py:26-37`, defaulting False, set True for this first sample). This keeps `resources.jsonl` the single carrier.
- [x] **Step 3: Run, verify pass.**

### Task P5.2 — Compute + persist the delta

- [x] **Step 1: Test** (`tests/test_merge.py` style): given samples where the baseline tick is 40000 MB and the peak in-window is 52000 MB, `merge`/aggregate yields `sys_used_delta_mb == 12000`.
- [x] **Step 2: Implement.** Thread the baseline (the `baseline=True` sample's `sys_used_mb`, or `min` of pre-first-request samples) through to per-cell aggregation; add `sys_used_delta_mb` to `RunRecord` (`models.py:52-95`) and `RAW_CSV_COLUMNS` (after `sys_used_mb`) — keep the import-time `RAW_CSV_COLUMNS ↔ RunRecord` assertion green. Add `sys_used_baseline_mb`/derived delta into `EvalResponse` + `_perf_summary` (`model_delta_gb`) + scores.csv.
- [x] **Step 3: UI labels.** In `compare.html`/`compare_axis.html`/`result.html`, relabel the existing peak as `ui.mlabel("system_peak_ram")` and add a `ui.mlabel("model_delta_ram")` value next to it; pressure stays `ui.mlabel("mem_pressure")`.
- [x] **Step 4: Document.** Add an AGENTS.md gotcha + a `docs/explanation/design-decisions.md` section: unified-memory caveat, baseline definition, what is NOT decomposed (KV), per-prompt isolation deferred.
- [x] **Step 5: Full `pytest` + `mypy` + `ruff`; restart; headless-verify compare/result; commit.**

```bash
git commit -am "feat(ram): baseline-subtracted model-delta as the cross-machine-comparable memory metric (P5)"
```

---

## Phase P6 — Reasoning-phase timing

**Files:** Modify `ramcheck/runner.py:67-78` (`RequestOutcome`) + `:88-164` (`stream_once`) + `:167-180`/derive, `ramcheck/results.py` (`EvalResponse`), `ramcheck/qualrun.py:189-207` (persist), compare/result UI. Tests `tests/test_runner.py`.

### Task P6.1 — Capture reasoning timing in `stream_once`

- [x] **Step 1: Test** (`tests/test_runner.py`, mirror the existing `ReasoningClient` + `SeqClock`): a client that yields two `reasoning_text` chunks then one `delta_text`; with an injected clock, assert the returned outcome has `reasoning_duration_s` ≈ (last reasoning tick − first reasoning tick) and that it is set only when reasoning arrived (NaN/None otherwise). Verify reasoning does NOT move TTFT (existing invariant).
- [x] **Step 2: Implement.** Add `t_reasoning_start: float | None = None` and `t_reasoning_last: float | None = None` to `RequestOutcome`. In `stream_once`'s loop, on each `ev.reasoning_text`: if `t_reasoning_start is None: t_reasoning_start = clock() - t0`; always `t_reasoning_last = clock() - t0`. After the loop compute `reasoning_duration_s = (t_reasoning_last - t_reasoning_start)` when both set else `math.nan`. Add a heuristic reasoning token count via `(counter or HeuristicCounter()).count(reasoning)` → `reasoning_completion_tokens`; `reasoning_tps = reasoning_completion_tokens / reasoning_duration_s` (nan-safe, mirror `derive_rates`).
- [x] **Step 3: Run, verify pass.**

### Task P6.2 — Persist + surface

- [x] **Step 1:** Add `reasoning_duration_s: float`, `reasoning_tps: float`, `reasoning_completion_tokens: int` to `EvalResponse` (`results.py`) with safe defaults; set them in `qualrun.py:189-207` from the outcome; they flow into `responses.jsonl` via `as_dict()`. Keep the slim policy: timing always; `reasoning_text` only on `content_empty` (unchanged).
- [x] **Step 2: Test** the `EvalResponse` round-trip (mirror `test_results.py`) — new fields default safely and serialize.
- [x] **Step 3: Surface** in `result.html` per-answer block (`ui.metric(reasoning_duration_s, "reasoning_duration", "s")`, `ui.metric(reasoning_tps, "reasoning_tps", "tok/s")`) and add a `Thinking`-row to `compare_axis.html` via a new `CompareCell.reasoning_*` median in `compare._cell_metrics`.
- [x] **Step 4: Full `pytest`/`mypy`/`ruff`; restart; headless-verify; commit.**

```bash
git commit -am "feat(eval): capture reasoning-phase duration + tps; surface thinking vs response split (P6)"
```

---

## Phase P0 — Tool rename (DEFERRED — blocked on target name)

**Blocked:** the user must provide the target name before execution (CLI `ramcheck`, package `llm-ramcheck`, repo `llm-benchmark-harness`).

- [ ] When the name is known: sweep `pyproject.toml` (`[project].name`, `[project.scripts]`), `ramcheck/` package dir, all imports, `AGENTS.md`, docs, configs, the GUI logo/title. Do it as a single mechanical commit (or a small series), full `pytest` green after.

---

## Final E2E smoke (before declaring done)

- [x] Start a real local endpoint; run `uv run ramcheck eval --pack packs/ndassist.yaml --config <local>.yaml` against ≥1 real model; open the GUI and walk every touched station (Konfig+Start, Übersicht/pack, Ergebnis, Vergleich, Export/Import). Confirm: per-answer perf renders with real numbers, RAM shows System-Peak + Modell-Delta, reasoning timing appears for a thinking model, export→import round-trips into `/compare`. Tests/review check logic; this checks real load (lesson `harness-preflight-and-rebuild`).

  > **DONE (2026-06-24):** Echter Smoke gegen LM Studio :1234 mit `google/gemma-4-12b-qat` (echtes Thinking-Modell, liefert `reasoning_content`), Judge `qwen/qwen3.6-27b`. Bundle `2026-06-24_133030_eval_ndassist` (6 Antworten, 0 Fehler, Qualität 76.2 % baseline / 88.8 % none). Live verifiziert: P3 Per-Answer-Perf (`tok/s`), P5 System-Peak 19.5/33.7 GB + Modell-Delta 1.2/15.5 GB, P6 Reasoning-Timing (`reasoning_duration_s`≈36 s, `reasoning_tps`≈16) + baseline-Sample als erste `resources.jsonl`-Zeile, P1 hwlabel-Demotion feuert im `/compare` (alte M1-Labels auf M5-Hardware ⚠), P4 config-view + YAML-Export, P7 ZIP-Export + Import-Round-Trip, sowie `judge_model`/`quant` im Report-Export. **TTFT-Invariante hält** (Uhr stoppt erst beim ersten Content-Token, nicht beim ersten Reasoning-Tick).

---

## Self-review — spec coverage

- Spec §2.1 macros → P2b. §2.2 glossary → P2a (+ completeness gate). P1 bug → P1. P2 explain → P2c. P3 perf/full-text + `e2e_med` rider → P3. P4 viewer/overrides/export → P4. P5 RAM delta → P5. P6 reasoning timing → P6. P7 export/import → P7. P0 rename → P0 (deferred). Out-of-scope items (dark mode, mobile, per-prompt KV, full YAML editor) are not tasked — correct.
- Sequence matches spec: P1 → P2 → P3 → (P4 ∥ P7) → P5 → P6 → P0.
