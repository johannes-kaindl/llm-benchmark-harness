# Judge Web-Monitor (`ramcheck judge --web`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only browser live-monitor for `ramcheck judge`, showing score distribution, red-flags, a verdict table, and an end-of-run weighted master-scorecard preview.

**Architecture:** Generalise `webmon.py` into transport (SSE + tail + serve) plus a pluggable *view module* selected by `--view eval|judge`. A new `judge_events.py` is the judge view (event contract + `build_view` + HTML, no resource panel). The judge run gains additive callbacks that append a separate `judge_events.jsonl`; the master `%`/safety math is computed in the host process and shipped pre-rendered. Default `judge` (no `--web`) is byte-identical.

**Tech Stack:** Python 3.12, stdlib `http.server` + hand-rolled SSE (zero new deps), pytest, ruff, mypy strict. Mirrors the existing `eval --web` (Ink. 3).

**Spec:** `docs/superpowers/specs/2026-06-20-judge-web-monitor-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `ramcheck/judge_events.py` | **new** — judge view: event constructors, `parse_line`, `build_view` (histogram/mean/red/ETA/master fold), `INDEX_HTML`, `TAILS_RESOURCES=False` |
| `ramcheck/events.py` | eval view: gains `INDEX_HTML` (moved out of webmon) + `TAILS_RESOURCES=True` |
| `ramcheck/webmon.py` | transport only: view-module dispatch (`--view`), HTML/aggregation from the view, tail `resources.jsonl` only when the view wants it |
| `ramcheck/runner.py` | `_WebMonitorProcess` gains an additive `view` param, passed to the subprocess |
| `ramcheck/cli.py` | `_live_monitor`/`_hold_monitor` gain `view`; `judge` refactored into `_judge_and_persist`/`_render_judge_scorecard`; new `_judge_event_writers`/`_master_rows`; `judge` gains `--web/--port/--no-open` |
| `AGENTS.md` | `judge --web` in command list + gotcha (judge view never tails `resources.jsonl`) |
| `tests/test_judge_events.py` | **new** — pure tests for the judge view |
| `tests/test_webmon.py`, `tests/test_cli_judge_web.py` | view dispatch + CLI writer + backward-compat |

The view interface (uniform across `events.py` and `judge_events.py`): module attributes `INDEX_HTML: str`, `TAILS_RESOURCES: bool`, and functions `parse_line(str) -> dict|None`, `build_view(Iterable[dict]) -> <obj with .as_dict()>`.

---

## Task 1: `judge_events.py` — event constructors + parsing

**Files:**
- Create: `ramcheck/judge_events.py`
- Test: `tests/test_judge_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge_events.py
from ramcheck import judge_events as je


def test_constructors_have_type_tags():
    assert je.judge_start_event(1.0, 7)["type"] == "judge_start"
    assert je.judge_start_event(1.0, 7)["total"] == 7
    v = je.verdict_event(2.0, 0, "m", "v", "p1", 0, "A", 4, False, False, "gut")
    assert v["type"] == "verdict" and v["model"] == "m" and v["score"] == 4
    assert v["red_flag"] is False and v["unscored"] is False and v["rationale"] == "gut"
    ms = je.master_event(3.0, "m", "v", 72.5, True, "", "Ja")
    assert ms["type"] == "master" and ms["pct"] == 72.5 and ms["recommendation"] == "Ja"
    assert je.judge_done_event(4.0, 5, 4)["type"] == "judge_done"


def test_verdict_event_truncates_rationale():
    v = je.verdict_event(1.0, 0, "m", "v", "p", 0, "A", 3, False, False, "x" * 500)
    assert len(v["rationale"]) == 160


def test_dumps_roundtrips_through_parse_line():
    line = je.dumps(je.judge_start_event(1.0, 3))
    assert je.parse_line(line) == {"ts": 1.0, "type": "judge_start", "total": 3}


def test_parse_line_tolerates_garbage():
    assert je.parse_line("") is None
    assert je.parse_line("   ") is None
    assert je.parse_line('{"truncated": ') is None  # half-written line
    assert je.parse_line("[1,2,3]") is None  # not a dict
    assert je.parse_line('{"no":"type"}') is None  # missing type tag
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_judge_events.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ramcheck.judge_events'`

- [ ] **Step 3: Write minimal implementation**

```python
# ramcheck/judge_events.py
"""Judge view for the live monitor — the scoring counterpart to events.py.

The judge loop fires callbacks (on_judge_start / on_verdict / master / on_judge_done);
the CLI's --web wiring turns them into lines of an append-only judge_events.jsonl. The
monitor subprocess reads those lines back and aggregates them with build_view(). Pure:
no I/O beyond (de)serialising dicts. Unlike the eval view it does NOT tail resources.jsonl
(the judge runs on a different endpoint; the bundle's resources.jsonl is the old eval run).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field

JUDGE_START = "judge_start"
VERDICT = "verdict"
MASTER = "master"
JUDGE_DONE = "judge_done"

TAILS_RESOURCES = False
RATIONALE_MAX = 160


def judge_start_event(ts: float, total: int) -> dict[str, object]:
    return {"ts": ts, "type": JUDGE_START, "total": total}


def verdict_event(
    ts: float,
    i: int,
    model: str,
    variant: str,
    prompt_id: str,
    repeat: int,
    category: str,
    score: int,
    red_flag: bool,
    unscored: bool,
    rationale: str,
) -> dict[str, object]:
    return {
        "ts": ts,
        "type": VERDICT,
        "i": i,
        "model": model,
        "variant": variant,
        "prompt_id": prompt_id,
        "repeat": repeat,
        "category": category,
        "score": score,
        "red_flag": red_flag,
        "unscored": unscored,
        "rationale": rationale[:RATIONALE_MAX],
    }


def master_event(
    ts: float,
    model: str,
    variant: str,
    pct: float,
    safety_passed: bool,
    safety_reason: str,
    recommendation: str,
) -> dict[str, object]:
    return {
        "ts": ts,
        "type": MASTER,
        "model": model,
        "variant": variant,
        "pct": pct,
        "safety_passed": safety_passed,
        "safety_reason": safety_reason,
        "recommendation": recommendation,
    }


def judge_done_event(ts: float, total: int, scored: int) -> dict[str, object]:
    return {"ts": ts, "type": JUDGE_DONE, "total": total, "scored": scored}


def dumps(event: dict[str, object]) -> str:
    return json.dumps(event, ensure_ascii=False)


def parse_line(line: str) -> dict[str, object] | None:
    """Parse one judge_events.jsonl line; None on empty/partial/malformed lines."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict) or "type" not in obj:
        return None
    return obj
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_judge_events.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/judge_events.py tests/test_judge_events.py
git commit -m "feat(judge_events): event contract + defensive parse for the judge view"
```

---

## Task 2: `judge_events.py` — `build_view` (histogram / mean / red / ETA / master fold)

**Files:**
- Modify: `ramcheck/judge_events.py` (append dataclasses + `build_view`)
- Test: `tests/test_judge_events.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_judge_events.py
def test_build_view_empty():
    v = je.build_view([])
    assert v.total == 0 and v.done == 0 and v.eta_s is None and v.finished is False
    assert v.mean_score is None and v.red_flags == 0
    assert v.histogram == {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}


def test_build_view_histogram_mean_red_and_unscored():
    events = [
        je.judge_start_event(0.0, 4),
        je.verdict_event(0.1, 0, "m", "v", "p1", 0, "A", 4, False, False, "ok"),
        je.verdict_event(0.2, 1, "m", "v", "p2", 0, "A", 2, True, False, "unsicher"),
        je.verdict_event(0.3, 2, "m", "v", "p3", 0, "B", 0, False, True, "judge weg"),
    ]
    v = je.build_view(events)
    assert v.total == 4 and v.done == 3
    assert v.histogram == {1: 0, 2: 1, 3: 0, 4: 1, 5: 0}  # unscored excluded
    assert v.red_flags == 1
    assert v.mean_score == 3.0  # mean of [4, 2], unscored excluded
    assert v.finished is False


def test_build_view_dedups_on_resume_replay():
    # same cell judged twice (crash+resume) → counts once, last wins
    events = [
        je.judge_start_event(0.0, 2),
        je.verdict_event(0.1, 0, "m", "v", "p1", 0, "A", 2, False, False, "first"),
        je.verdict_event(0.2, 0, "m", "v", "p1", 0, "A", 5, False, False, "second"),
    ]
    v = je.build_view(events)
    assert v.done == 1 and v.histogram[5] == 1 and v.histogram[2] == 0
    assert v.verdicts[0].rationale == "second"


def test_build_view_eta_ignores_replay_burst():
    # prior burst written ~instantly (tiny gaps) must not crush the ETA
    events = [
        je.judge_start_event(0.0, 5),
        je.verdict_event(100.00, 0, "m", "v", "p1", 0, "A", 3, False, False, "a"),
        je.verdict_event(100.00, 1, "m", "v", "p2", 0, "A", 3, False, False, "b"),  # burst
        je.verdict_event(110.0, 2, "m", "v", "p3", 0, "A", 3, False, False, "c"),  # 10s real gap
    ]
    v = je.build_view(events)
    assert v.done == 3
    assert v.eta_s is not None and abs(v.eta_s - 20.0) < 0.01  # 10s/gap × 2 remaining


def test_build_view_masters_and_finished():
    events = [
        je.judge_start_event(0.0, 1),
        je.verdict_event(0.1, 0, "m", "v", "p1", 0, "A", 3, False, False, "x"),
        je.master_event(0.2, "m", "v", 40.0, False, "Q6 ≤ 2", "Nein"),
        je.judge_done_event(0.3, 1, 1),
    ]
    v = je.build_view(events)
    assert v.finished is True and len(v.masters) == 1
    assert v.masters[0].pct == 40.0 and v.masters[0].safety_passed is False
    assert v.masters[0].recommendation == "Nein"
    d = v.as_dict()
    assert d["histogram"] == {"1": 0, "2": 0, "3": 1, "4": 0, "5": 0}  # str keys for JSON
    assert d["masters"][0]["safety_reason"] == "Q6 ≤ 2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_judge_events.py -q`
Expected: FAIL — `AttributeError: module 'ramcheck.judge_events' has no attribute 'build_view'`

- [ ] **Step 3: Write minimal implementation**

Append to `ramcheck/judge_events.py`:

```python
VerdictKey = tuple[str, str, str, int]  # (model, variant, prompt_id, repeat)
ETA_GAP_FLOOR = 0.05  # ignore sub-50ms gaps (the resume replay burst) when estimating ETA


def _as_int(x: object, default: int = 0) -> int:
    return int(x) if isinstance(x, (int, float, str)) else default


def _as_float(x: object, default: float = 0.0) -> float:
    return float(x) if isinstance(x, (int, float, str)) else default


@dataclass
class VerdictView:
    key: VerdictKey
    i: int
    model: str
    variant: str
    prompt_id: str
    category: str
    score: int
    red_flag: bool
    unscored: bool
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "i": self.i,
            "model": self.model,
            "variant": self.variant,
            "prompt_id": self.prompt_id,
            "category": self.category,
            "score": self.score,
            "red_flag": self.red_flag,
            "unscored": self.unscored,
            "rationale": self.rationale,
        }


@dataclass
class MasterRow:
    model: str
    variant: str
    pct: float
    safety_passed: bool
    safety_reason: str
    recommendation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "variant": self.variant,
            "pct": self.pct,
            "safety_passed": self.safety_passed,
            "safety_reason": self.safety_reason,
            "recommendation": self.recommendation,
        }


@dataclass
class JudgeRunView:
    total: int = 0
    done: int = 0
    histogram: dict[int, int] = field(default_factory=lambda: {s: 0 for s in (1, 2, 3, 4, 5)})
    red_flags: int = 0
    mean_score: float | None = None
    eta_s: float | None = None
    verdicts: list[VerdictView] = field(default_factory=list)
    masters: list[MasterRow] = field(default_factory=list)
    finished: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "done": self.done,
            "histogram": {str(k): v for k, v in self.histogram.items()},
            "red_flags": self.red_flags,
            "mean_score": self.mean_score,
            "eta_s": self.eta_s,
            "finished": self.finished,
            "verdicts": [v.as_dict() for v in self.verdicts],
            "masters": [m.as_dict() for m in self.masters],
        }


def _key(ev: dict[str, object]) -> VerdictKey:
    return (str(ev["model"]), str(ev["variant"]), str(ev["prompt_id"]), _as_int(ev["repeat"]))


def _eta(ts_list: list[float], total: int, done: int) -> float | None:
    gaps = [b - a for a, b in zip(ts_list, ts_list[1:]) if (b - a) > ETA_GAP_FLOOR]
    if not gaps or total <= done:
        return None
    return (sum(gaps) / len(gaps)) * (total - done)


def build_view(events: Iterable[dict[str, object]]) -> JudgeRunView:
    """Fold a judge event stream into a render-ready view. Verdicts dedup by cell key
    (last wins) so a cell re-judged across crash+resume counts once."""
    total = 0
    finished = False
    by_key: dict[VerdictKey, VerdictView] = {}
    order: list[VerdictKey] = []
    masters_by: dict[tuple[str, str], MasterRow] = {}
    master_order: list[tuple[str, str]] = []
    ts_list: list[float] = []
    for e in events:
        t = e.get("type")
        if t == JUDGE_START:
            total = max(total, _as_int(e.get("total", 0)))
        elif t == JUDGE_DONE:
            finished = True
        elif t == VERDICT:
            k = _key(e)
            if k not in by_key:
                order.append(k)
            by_key[k] = VerdictView(
                key=k,
                i=_as_int(e.get("i", -1), -1),
                model=str(e["model"]),
                variant=str(e["variant"]),
                prompt_id=str(e["prompt_id"]),
                category=str(e.get("category", "")),
                score=_as_int(e.get("score", 0)),
                red_flag=bool(e.get("red_flag")),
                unscored=bool(e.get("unscored")),
                rationale=str(e.get("rationale", "")),
            )
            ts_list.append(_as_float(e.get("ts", 0.0)))
        elif t == MASTER:
            mk = (str(e["model"]), str(e["variant"]))
            if mk not in masters_by:
                master_order.append(mk)
            masters_by[mk] = MasterRow(
                model=mk[0],
                variant=mk[1],
                pct=_as_float(e.get("pct", 0.0)),
                safety_passed=bool(e.get("safety_passed")),
                safety_reason=str(e.get("safety_reason", "")),
                recommendation=str(e.get("recommendation", "")),
            )
    verdicts = [by_key[k] for k in order]
    scored = [v for v in verdicts if not v.unscored]
    histogram = {s: sum(1 for v in scored if v.score == s) for s in (1, 2, 3, 4, 5)}
    mean = (sum(v.score for v in scored) / len(scored)) if scored else None
    return JudgeRunView(
        total=total,
        done=len(verdicts),
        histogram=histogram,
        red_flags=sum(1 for v in scored if v.red_flag),
        mean_score=mean,
        eta_s=_eta(ts_list, total, len(verdicts)),
        verdicts=verdicts,
        masters=[masters_by[k] for k in master_order],
        finished=finished,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_judge_events.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/judge_events.py tests/test_judge_events.py
git commit -m "feat(judge_events): build_view — histogram, mean, red, ETA, master fold, key-dedup"
```

---

## Task 3: `judge_events.py` — `INDEX_HTML` dashboard

**Files:**
- Modify: `ramcheck/judge_events.py` (add `INDEX_HTML`)
- Test: `tests/test_judge_events.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_judge_events.py
def test_index_html_is_judge_dashboard():
    html = je.INDEX_HTML
    assert "EventSource" in html  # SSE client present
    assert "Score" in html and "Master" in html  # judge-specific panels
    assert "Throttle" not in html  # no load panel (J5)
    assert je.TAILS_RESOURCES is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_judge_events.py::test_index_html_is_judge_dashboard -q`
Expected: FAIL — `AttributeError: module 'ramcheck.judge_events' has no attribute 'INDEX_HTML'`

- [ ] **Step 3: Write minimal implementation**

Append to `ramcheck/judge_events.py`:

```python
INDEX_HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>ramcheck judge monitor</title>
<style>
 body{font-family:system-ui,sans-serif;margin:1.5rem;background:#111;color:#eee}
 h1{font-size:1.1rem} h2{font-size:1rem;margin-top:1.5rem}
 .bar{background:#333;border-radius:4px;height:1.2rem;overflow:hidden}
 .bar>div{background:#3a7;height:100%;width:0;transition:width .3s}
 .grid{display:flex;gap:1rem;margin:1rem 0;flex-wrap:wrap}
 .card{background:#1b1b1b;padding:.7rem 1rem;border-radius:6px;min-width:6rem}
 .num{font-size:1.3rem;font-weight:600}
 table{border-collapse:collapse;width:100%;font-size:.85rem;margin-top:.5rem}
 td,th{padding:.25rem .5rem;border-bottom:1px solid #2a2a2a;text-align:left}
 .ok{color:#5c5} .fail{color:#e66} .muted{color:#999}
</style></head>
<body>
<h1>ramcheck — live judge monitor</h1>
<div class="bar"><div id="barfill"></div></div>
<div class="grid">
 <div class="card"><div class="muted">Fortschritt</div><div class="num"><span id="done">0</span>/<span id="total">0</span></div></div>
 <div class="card"><div class="muted">&Oslash; Score</div><div class="num" id="mean">&ndash;</div></div>
 <div class="card"><div class="muted">Red-Flags</div><div class="num fail" id="red">0</div></div>
 <div class="card"><div class="muted">ETA</div><div class="num" id="eta">&ndash;</div></div>
</div>
<h2>Score-Verteilung</h2>
<div class="grid" id="histwrap"></div>
<h2>Verdicts</h2>
<table><thead><tr><th>#</th><th>Prompt</th><th>Modell &middot; Variante</th><th>Score</th><th>Red?</th><th>Begr&uuml;ndung</th></tr></thead>
<tbody id="rows"></tbody></table>
<h2>Master-Scorecard (am Ende)</h2>
<table><thead><tr><th>Modell &middot; Variante</th><th>In %</th><th>Sicherheit</th><th>Empfehlung</th></tr></thead>
<tbody id="masterbody"></tbody></table>
<script>
function fmtEta(s){if(s==null)return '\\u2013';s=Math.round(s);return Math.floor(s/60)+'m '+(s%60)+'s';}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
const es=new EventSource('/events');
es.addEventListener('view',e=>{let v;try{v=JSON.parse(e.data)}catch(_){return}
 done.textContent=v.done; total.textContent=v.total;
 mean.textContent=v.mean_score!=null?v.mean_score.toFixed(2):'\\u2013';
 red.textContent=v.red_flags;
 eta.textContent=v.finished?'fertig':fmtEta(v.eta_s);
 barfill.style.width=(v.total?100*v.done/v.total:0)+'%';
 histwrap.innerHTML=[1,2,3,4,5].map(s=>`<div class="card"><div class="muted">Score ${s}</div><div class="num">${(v.histogram&&v.histogram[s])||0}</div></div>`).join('');
 rows.innerHTML=v.verdicts.slice().reverse().map(c=>{
  const sc=c.unscored?'<span class="muted">\\u2014</span>':c.score;
  const rf=c.red_flag?'<span class="fail">\\ud83d\\udd34</span>':'';
  return `<tr><td>${c.i}</td><td>${esc(c.prompt_id)}</td><td>${esc(c.model)} \\u00b7 ${esc(c.variant)}</td><td>${sc}</td><td>${rf}</td><td class="muted">${esc(c.rationale)}</td></tr>`;
 }).join('');
 masterbody.innerHTML=v.masters.map(m=>{
  const safe=m.safety_passed?'<span class="ok">ja</span>':`<span class="fail">nein</span> <span class="muted">(${esc(m.safety_reason)})</span>`;
  return `<tr><td>${esc(m.model)} \\u00b7 ${esc(m.variant)}</td><td>${m.pct.toFixed(1)} %</td><td>${safe}</td><td>${esc(m.recommendation)}</td></tr>`;
 }).join('');});
es.onerror=()=>{document.title='ramcheck judge monitor (offline)';};
</script></body></html>"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_judge_events.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/judge_events.py tests/test_judge_events.py
git commit -m "feat(judge_events): read-only judge dashboard HTML (no load panel)"
```

---

## Task 4: `events.py` + `webmon.py` — extract eval HTML, view-module dispatch (eval only)

This is a refactor: move the eval `INDEX_HTML` from `webmon.py` into `events.py`, give the eval view a `TAILS_RESOURCES=True` flag, and make `webmon` resolve a *view module* (only `eval` registered yet). External behaviour is unchanged — existing `test_webmon.py` stays green.

**Files:**
- Modify: `ramcheck/events.py` (add `INDEX_HTML` + `TAILS_RESOURCES`)
- Modify: `ramcheck/webmon.py:24-149` (dispatch via view module)
- Test: existing `tests/test_webmon.py` (regression — no change)

- [ ] **Step 1: Add `INDEX_HTML` + `TAILS_RESOURCES` to `events.py`**

At the top of `ramcheck/events.py` (after the imports / type-tag constants), add:

```python
TAILS_RESOURCES = True
```

Then append the eval dashboard HTML — **move it verbatim** from `webmon.py` (current lines 24–68, the `INDEX_HTML = """..."""` block) into `events.py` as a module-level `INDEX_HTML`. Cut it from `webmon.py`.

- [ ] **Step 2: Rewrite `webmon.make_handler` + `main` to dispatch on a view module**

Replace `ramcheck/webmon.py` lines 18–22 imports and the `make_handler`/`main` functions with:

```python
from ramcheck import events as events_mod
from ramcheck import judge_events as judge_events_mod
from ramcheck import loadview as loadview_mod
from ramcheck import tail as tail_mod

POLL_S = 0.25

_VIEWS = {"eval": events_mod, "judge": judge_events_mod}
```

```python
def make_handler(
    bundle: str | Path, events_name: str = "events.jsonl", view: str = "eval"
) -> type[BaseHTTPRequestHandler]:
    bundle_dir = Path(bundle)
    events_path = bundle_dir / events_name
    resources_path = bundle_dir / "resources.jsonl"
    view_mod = _VIEWS[view]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # keep stdout clean (parent reads port)
            pass

        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/?"):
                self._serve_index()
            elif self.path.startswith("/events"):
                self._serve_sse()
            else:
                self.send_error(404)

        def _serve_index(self) -> None:
            body = view_mod.INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            all_events: list[dict[str, object]] = []
            offset = 0
            try:
                while True:
                    lines, offset = tail_mod.read_new(events_path, offset)
                    for ln in lines:
                        parsed = view_mod.parse_line(ln)
                        if parsed is not None:
                            all_events.append(parsed)
                    view_obj = view_mod.build_view(all_events)
                    self._send("view", json.dumps(view_obj.as_dict()))
                    if view_mod.TAILS_RESOURCES:
                        load = loadview_mod.latest_load(resources_path)
                        if load is not None:
                            self._send(
                                "load",
                                json.dumps(
                                    {
                                        "sys_used_mb": load.sys_used_mb,
                                        "mem_pressure": load.mem_pressure,
                                        "throttled": load.throttled,
                                        "any_throttle_seen": load.any_throttle_seen,
                                    }
                                ),
                            )
                    time.sleep(POLL_S)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return  # browser closed the connection
            except Exception:
                return  # any other error → end this stream cleanly, never crash the thread

        def _send(self, kind: str, data: str) -> None:
            self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode())
            self.wfile.flush()

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="ramcheck live monitor server")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--events", default="events.jsonl")
    ap.add_argument("--view", default="eval", choices=sorted(_VIEWS))
    args = ap.parse_args(argv)
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), make_handler(args.bundle, args.events, args.view)
    )
    print(server.server_address[1], flush=True)  # parent reads this to open the browser
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
```

- [ ] **Step 3: Run the existing webmon + events tests (regression)**

Run: `uv run pytest tests/test_webmon.py tests/test_events.py -q`
Expected: PASS — eval index still serves HTML with `EventSource`, SSE still streams `view` + `load`.

- [ ] **Step 4: Lint + type-check the touched files**

Run: `uv run ruff check ramcheck/webmon.py ramcheck/events.py && uv run mypy ramcheck/webmon.py ramcheck/events.py ramcheck/judge_events.py`
Expected: clean

- [ ] **Step 5: Commit**

```bash
git add ramcheck/webmon.py ramcheck/events.py
git commit -m "refactor(webmon): transport + pluggable view module (eval HTML → events.py)"
```

---

## Task 5: `webmon` + `runner` — register judge view, `_WebMonitorProcess` view param

**Files:**
- Modify: `ramcheck/runner.py:336-354` (`_WebMonitorProcess.__init__` + `start`)
- Test: `tests/test_webmon.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_webmon.py
def test_judge_view_serves_judge_html_without_load(tmp_path):
    (tmp_path / "judge_events.jsonl").write_text(
        json.dumps({"ts": 1.0, "type": "judge_start", "total": 1})
        + "\n"
        + json.dumps(
            {
                "ts": 2.0,
                "type": "verdict",
                "i": 0,
                "model": "m",
                "variant": "v",
                "prompt_id": "p",
                "repeat": 0,
                "category": "A",
                "score": 4,
                "red_flag": False,
                "unscored": False,
                "rationale": "gut",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0), webmon.make_handler(tmp_path, "judge_events.jsonl", "judge")
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=3)
        c.request("GET", "/")
        body = c.getresponse().read().decode("utf-8")
        assert "judge monitor" in body and "Master" in body
        c2 = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c2.request("GET", "/events")
        r = c2.getresponse()
        buf = b""
        for _ in range(40):
            buf += r.read(256)
            if b"event: view" in buf:
                break
        text = buf.decode("utf-8", errors="replace")
        assert "event: view" in text
        assert "event: load" not in text  # judge view never tails resources (J5)
        view_line = next(
            ln
            for blk in text.split("\n\n")
            if "event: view" in blk
            for ln in blk.split("\n")
            if ln.startswith("data: ")
        )
        view = json.loads(view_line[len("data: ") :])
        assert view["total"] == 1 and view["done"] == 1 and view["histogram"]["4"] == 1
    finally:
        c2.close()
        srv.shutdown()


def test_webmonitor_process_passes_view(tmp_path):
    (tmp_path / "judge_events.jsonl").write_text("", encoding="utf-8")
    mon = _WebMonitorProcess(tmp_path, port=0, events_name="judge_events.jsonl", view="judge")
    port = mon.start()
    try:
        assert isinstance(port, int) and port > 0
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("GET", "/")
        assert "judge monitor" in c.getresponse().read().decode("utf-8")
    finally:
        mon.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webmon.py::test_webmonitor_process_passes_view -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'view'`

- [ ] **Step 3: Add the `view` param to `_WebMonitorProcess`**

In `ramcheck/runner.py`, change `_WebMonitorProcess.__init__` (line ~336):

```python
    def __init__(
        self, bundle: Path, port: int = 0, events_name: str = "events.jsonl", view: str = "eval"
    ) -> None:
        self.bundle = bundle
        self.port = port
        self.events_name = events_name
        self.view = view
        self._proc: subprocess.Popen[bytes] | None = None
```

And in `start()` extend the `cmd` list (after the `--events` pair, line ~353):

```python
            "--events",
            self.events_name,
            "--view",
            self.view,
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webmon.py -q`
Expected: PASS (all webmon tests, incl. the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/runner.py tests/test_webmon.py
git commit -m "feat(webmon): register judge view + thread --view through _WebMonitorProcess"
```

---

## Task 6: `cli.py` — `view` param on `_live_monitor` / `_hold_monitor`

**Files:**
- Modify: `ramcheck/cli.py:209-226` (`_live_monitor`)
- Test: covered by Task 9's CLI test (no standalone test — pure pass-through)

- [ ] **Step 1: Thread `view` through `_live_monitor`**

In `ramcheck/cli.py`, change the `_live_monitor` signature + the `_WebMonitorProcess` construction:

```python
@contextlib.contextmanager
def _live_monitor(
    run_dir: Path,
    port: int,
    no_open: bool,
    events_name: str = "events.jsonl",
    view: str = "eval",
) -> Iterator[tuple[_WebMonitorProcess, str | None]]:
    """Spawn the live-monitor subprocess, open the browser, and always stop it on exit."""
    monitor = _WebMonitorProcess(run_dir, port=port, events_name=events_name, view=view)
    bound = monitor.start()
```

(The rest of `_live_monitor` and all of `_hold_monitor` are unchanged.)

- [ ] **Step 2: Verify eval path still type-checks and runs**

Run: `uv run mypy ramcheck/cli.py && uv run pytest tests/ -q -k "webmon or events or eval or cli"`
Expected: clean + PASS (eval `--web` wiring still passes `events_name` only; `view` defaults to `eval`)

- [ ] **Step 3: Commit**

```bash
git add ramcheck/cli.py
git commit -m "feat(cli): view param on _live_monitor (default eval, pass-through)"
```

---

## Task 7: `cli.py` — refactor `judge` into `_judge_and_persist` + `_render_judge_scorecard`

Pure refactor of the existing `judge` command into two reusable helpers. No behaviour change; the existing judge flow stays identical.

**Files:**
- Modify: `ramcheck/cli.py:446-499` (the `judge` command body)
- Test: existing judge/CLI tests stay green

- [ ] **Step 1: Add the two helpers above the `judge` command**

Insert into `ramcheck/cli.py` (just before `@app.command()` / `def judge`):

```python
def _judge_and_persist(
    backend: object,
    responses: list[EvalResponse],
    pk: object,
    prior: list[Verdict],
    jpath: Path,
    *,
    on_verdict: Callable[[Verdict], None] | None = None,
) -> tuple[list[Verdict], list[ModelReport]]:
    """Judge fresh responses, persist judgements.jsonl (append-stream then clean rewrite),
    return (verdicts, reports). ``on_verdict`` (web) is called in addition to the append."""
    with jpath.open("a", encoding="utf-8") as jh:

        def _append(v: Verdict) -> None:
            jh.write(json.dumps(v.as_dict(), ensure_ascii=False) + "\n")
            jh.flush()
            if on_verdict is not None:
                on_verdict(v)

        verdicts, reports = judge_bundle(
            backend, responses, pk, prior_verdicts=prior, on_verdict=_append  # type: ignore[arg-type]
        )
    with jpath.open("w", encoding="utf-8") as jh:  # clean rewrite: prior + new, deduped
        for v in verdicts:
            jh.write(json.dumps(v.as_dict(), ensure_ascii=False) + "\n")
    return verdicts, reports


def _render_judge_scorecard(
    bundle: Path,
    pk: object,
    responses: list[EvalResponse],
    verdicts: list[Verdict],
    reports: list[ModelReport],
    host: dict[str, str],
) -> None:
    md = scorecard_mod.render_scorecard_md(
        pk, responses, verdicts, reports, host=host, date_str=_today()  # type: ignore[arg-type]
    )
    (bundle / "scorecard.md").write_text(md, encoding="utf-8")
    rows = scorecard_mod.scores_csv_rows(pk, responses, verdicts, reports, host=host)  # type: ignore[arg-type]
    if rows:
        with (bundle / "scores.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
```

> Note: `ModelReport` must be imported in `cli.py`. If it is not already, add it to the
> `from ramcheck.results import ...` line (alongside `EvalResponse`, `Verdict`).

- [ ] **Step 2: Replace the body of the `judge` command (from `jpath = ...` to the end) with helper calls**

```python
    jpath = bundle / "judgements.jsonl"
    verdicts, reports = _judge_and_persist(backend, responses, pk, prior, jpath)
    _render_judge_scorecard(bundle, pk, responses, verdicts, reports, host)
    scored = sum(1 for v in verdicts if not v.unscored)
    console.print(
        f"[green]✓[/] {scored}/{len(verdicts)} bewertet · [bold]{bundle / 'scorecard.md'}[/]"
    )
```

- [ ] **Step 3: Run the full suite (refactor must not change behaviour)**

Run: `uv run pytest tests/ -q`
Expected: PASS (same count as before this task)

- [ ] **Step 4: Lint + type-check**

Run: `uv run ruff check ramcheck/cli.py && uv run mypy ramcheck/`
Expected: clean

- [ ] **Step 5: Commit**

```bash
git add ramcheck/cli.py
git commit -m "refactor(cli): extract _judge_and_persist + _render_judge_scorecard (no behaviour change)"
```

---

## Task 8: `cli.py` — `_judge_event_writers` + `_master_rows`

**Files:**
- Modify: `ramcheck/cli.py` (add the two helpers near `_eval_event_writers`)
- Test: `tests/test_cli_judge_web.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_judge_web.py
import json

from ramcheck import cli
from ramcheck.results import Verdict


def _v(prompt_id, score, red=False, unscored=False, model="m", variant="v"):
    return Verdict(
        model=model,
        variant=variant,
        prompt_id=prompt_id,
        repeat=0,
        category="A",
        score=score,
        red_flag=red,
        rationale="r-" + prompt_id,
        unscored=unscored,
    )


def _read(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_judge_event_writers_start_replays_prior(tmp_path):
    p = tmp_path / "judge_events.jsonl"
    on_start, on_verdict, write_masters, done = cli._judge_event_writers(p)
    on_start(3, [_v("p1", 4), _v("p2", 2, red=True)])
    rows = _read(p)
    assert rows[0]["type"] == "judge_start" and rows[0]["total"] == 3
    assert [r["type"] for r in rows[1:]] == ["verdict", "verdict"]
    assert rows[1]["i"] == 0 and rows[2]["i"] == 1 and rows[2]["red_flag"] is True


def test_judge_event_writers_fresh_verdict_continues_counter(tmp_path):
    p = tmp_path / "judge_events.jsonl"
    on_start, on_verdict, write_masters, done = cli._judge_event_writers(p)
    on_start(2, [_v("p1", 4)])
    on_verdict(_v("p2", 5))
    rows = _read(p)
    verdicts = [r for r in rows if r["type"] == "verdict"]
    assert verdicts[1]["i"] == 1 and verdicts[1]["prompt_id"] == "p2"


def test_judge_event_writers_masters_and_done(tmp_path):
    p = tmp_path / "judge_events.jsonl"
    on_start, on_verdict, write_masters, done = cli._judge_event_writers(p)
    on_start(1, [])
    write_masters([
        {
            "model": "m",
            "variant": "v",
            "pct": 40.0,
            "safety_passed": False,
            "safety_reason": "Q6",
            "recommendation": "Nein",
        }
    ])
    done(1, 1)
    rows = _read(p)
    assert rows[-2]["type"] == "master" and rows[-2]["pct"] == 40.0
    assert rows[-1]["type"] == "judge_done" and rows[-1]["scored"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_judge_web.py -q`
Expected: FAIL — `AttributeError: module 'ramcheck.cli' has no attribute '_judge_event_writers'`

- [ ] **Step 3: Write the implementation**

Add to `ramcheck/cli.py` (import the module + add helpers near `_eval_event_writers`). First ensure the import exists at the top:

```python
from ramcheck import judge_events as judge_events_mod
```

Then add:

```python
def _judge_event_writers(
    events_path: Path,
) -> tuple[
    Callable[[int, list[Verdict]], None],
    Callable[[Verdict], None],
    Callable[[list[dict[str, object]]], None],
    Callable[[int, int], None],
]:
    """Closures translating the judge run into judge_events.jsonl lines.

    Returns (on_judge_start, on_verdict, write_masters, judge_done). ``on_judge_start``
    writes the total and replays prior verdicts (resume) so the dashboard seeds correctly.
    A shared counter gives every verdict a stable display index."""
    fh = events_path.open("a", encoding="utf-8")
    counter = {"i": 0}

    def _w(event: dict[str, object]) -> None:
        fh.write(judge_events_mod.dumps(event) + "\n")
        fh.flush()

    def _verdict(v: Verdict) -> None:
        i = counter["i"]
        counter["i"] += 1
        _w(
            judge_events_mod.verdict_event(
                time.time(),
                i,
                v.model,
                v.variant,
                v.prompt_id,
                v.repeat,
                v.category,
                v.score,
                v.red_flag,
                v.unscored,
                v.rationale,
            )
        )

    def on_judge_start(total: int, prior: list[Verdict]) -> None:
        _w(judge_events_mod.judge_start_event(time.time(), total))
        for v in prior:
            _verdict(v)

    def on_verdict(v: Verdict) -> None:
        _verdict(v)

    def write_masters(rows: list[dict[str, object]]) -> None:
        for row in rows:
            _w(
                judge_events_mod.master_event(
                    time.time(),
                    str(row["model"]),
                    str(row["variant"]),
                    float(row["pct"]),  # type: ignore[arg-type]
                    bool(row["safety_passed"]),
                    str(row["safety_reason"]),
                    str(row["recommendation"]),
                )
            )

    def judge_done(total: int, scored: int) -> None:
        try:
            _w(judge_events_mod.judge_done_event(time.time(), total, scored))
        finally:
            fh.close()

    return on_judge_start, on_verdict, write_masters, judge_done


def _master_rows(
    pk: object,
    responses: list[EvalResponse],
    verdicts: list[Verdict],
    reports: list[ModelReport],
) -> list[dict[str, object]]:
    """Per-(model, variant) master summary, computed in the host process (J6): the monitor
    only displays it. Reuses scorecard's rules so the dashboard matches scorecard.md."""
    reports_by = {(r.model, r.variant): r for r in reports}
    rows: list[dict[str, object]] = []
    for model, variant in scorecard_mod.model_variant_groups(responses):
        rep = reports_by.get((model, variant))
        if not (rep and rep.dim_scores):
            continue
        _, _, pct = scorecard_mod.weighted_total(rep.dim_scores, pk)  # type: ignore[arg-type]
        gv = [v for v in verdicts if (v.model, v.variant) == (model, variant)]
        passed, reason = scorecard_mod.passes_ko(
            rep.dim_scores, scorecard_mod.red_flagged_prompts(gv), pk  # type: ignore[arg-type]
        )
        rows.append(
            {
                "model": model,
                "variant": variant,
                "pct": pct,
                "safety_passed": passed,
                "safety_reason": reason,
                "recommendation": scorecard_mod._recommendation(passed, pct),
            }
        )
    return rows
```

> `_recommendation` is intentionally reused from `scorecard.py` so the dashboard's verdict
> matches the rendered `scorecard.md` exactly (single source of the recommendation rule).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_judge_web.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check ramcheck/cli.py && uv run mypy ramcheck/`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add ramcheck/cli.py tests/test_cli_judge_web.py
git commit -m "feat(cli): judge event writers + host-side master rows"
```

---

## Task 9: `cli.py` — `judge --web` branch + backward-compat guard

**Files:**
- Modify: `ramcheck/cli.py` (`judge` command signature + body)
- Test: `tests/test_cli_judge_web.py` (append)

- [ ] **Step 1: Write the failing test (backward-compat — default path writes no judge_events.jsonl)**

```python
# append to tests/test_cli_judge_web.py
import typer.testing

from ramcheck import judge as judge_mod
from ramcheck import scorecard as scorecard_mod_t


def test_judge_without_web_writes_no_judge_events(tmp_path, monkeypatch):
    # Minimal bundle: one response, a fake judge backend, no --web.
    from ramcheck.cli import app
    from tests.fixtures_judge import write_minimal_bundle, FakeBackendFactory  # see Step 3 note

    bundle = write_minimal_bundle(tmp_path)
    monkeypatch.setattr("ramcheck.cli.OpenAIJudgeBackend", FakeBackendFactory)
    runner = typer.testing.CliRunner()
    result = runner.invoke(
        app,
        ["judge", "--bundle", str(bundle), "--judge-config", str(bundle / "judge.yaml")],
    )
    assert result.exit_code == 0, result.output
    assert (bundle / "scorecard.md").exists()
    assert not (bundle / "judge_events.jsonl").exists()  # no --web → no event file
```

> **Step 3 note (fixtures):** if a shared bundle/fake-backend fixture does not already
> exist under `tests/`, create `tests/fixtures_judge.py` with `write_minimal_bundle(tmp_path)`
> (writes `bundle.json`, `responses.jsonl` with one `EvalResponse`, a `judge.yaml`, and a
> `packs/` reference the manifest points to) and a `FakeBackendFactory(*a, **k)` returning a
> backend whose `.judge()` returns `'{"score": 4, "red_flag": false, "rationale": "ok"}'`.
> Model the bundle on an existing eval-bundle test if one exists (grep `tests/` for
> `responses.jsonl` / `bundle.json` first and reuse that helper instead of duplicating).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_judge_web.py::test_judge_without_web_writes_no_judge_events -q`
Expected: FAIL — initially an import/fixture error or, once fixtures exist, it passes only after the `--web` option is added without breaking the default path. (If it already passes because the default path is untouched, that's the regression guarantee — keep it.)

- [ ] **Step 3: Add `--web/--port/--no-open` to the `judge` command + the web branch**

Change the `judge` command signature (add three options after `judge_config`):

```python
@app.command()
def judge(
    bundle: Path = typer.Option(..., "--bundle", exists=True, help="an eval bundle dir"),
    judge_config: Path | None = typer.Option(
        None, "--judge-config", help="judge endpoint YAML (omit → leave unscored)"
    ),
    web: bool = typer.Option(False, "--web", help="live browser monitor for this judging run"),
    port: int = typer.Option(0, "--port", help="monitor port (0 = auto)"),
    no_open: bool = typer.Option(False, "--no-open", help="don't auto-open the browser"),
) -> None:
```

Replace the helper-call body (added in Task 7) with a branch:

```python
    jpath = bundle / "judgements.jsonl"
    if not web:
        verdicts, reports = _judge_and_persist(backend, responses, pk, prior, jpath)
        _render_judge_scorecard(bundle, pk, responses, verdicts, reports, host)
        scored = sum(1 for v in verdicts if not v.unscored)
        console.print(
            f"[green]✓[/] {scored}/{len(verdicts)} bewertet · [bold]{bundle / 'scorecard.md'}[/]"
        )
        return

    with _live_monitor(
        bundle, port, no_open, events_name="judge_events.jsonl", view="judge"
    ) as (monitor, url):
        on_judge_start, on_verdict, write_masters, judge_done = _judge_event_writers(
            bundle / "judge_events.jsonl"
        )
        on_judge_start(len(responses), prior)
        verdicts: list[Verdict] = []
        reports: list[ModelReport] = []
        try:
            verdicts, reports = _judge_and_persist(
                backend, responses, pk, prior, jpath, on_verdict=on_verdict
            )
            write_masters(_master_rows(pk, responses, verdicts, reports))
        finally:
            judge_done(len(verdicts), sum(1 for v in verdicts if not v.unscored))
        _render_judge_scorecard(bundle, pk, responses, verdicts, reports, host)
        scored = sum(1 for v in verdicts if not v.unscored)
        console.print(
            f"[green]✓[/] {scored}/{len(verdicts)} bewertet · [bold]{bundle / 'scorecard.md'}[/]"
        )
        _hold_monitor(monitor, url)
```

- [ ] **Step 4: Run the judge-web tests + full suite**

Run: `uv run pytest tests/ -q`
Expected: PASS (all tests, incl. backward-compat guard)

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy ramcheck/`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add ramcheck/cli.py tests/test_cli_judge_web.py tests/fixtures_judge.py
git commit -m "feat(cli): judge --web live monitor branch (default path byte-identical)"
```

---

## Task 10: Docs + full verification

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Document `judge --web`**

In `AGENTS.md`, in the `## Commands` block, add after the existing `judge` line:

```bash
uv run ramcheck judge  --bundle runs/<ts>_eval_ndassist --judge-config judge.yaml --web  # + live judge monitor
```

In the `## Gotchas` block, append:

```markdown
- **The judge monitor never tails `resources.jsonl`.** `judge --web` runs the judge on a
  *different* endpoint (a cloud/local judge), so the bundle's `resources.jsonl` (from the
  earlier `eval` run) is stale and irrelevant. The judge view (`ramcheck/judge_events.py`,
  `TAILS_RESOURCES=False`) shows score distribution / red-flags / a master-scorecard preview
  instead of a load panel. The master `%`/safety values are computed in the host process and
  shipped pre-rendered — the monitor never sees the `Pack`.
```

Also update the module map in `## Architecture principles` (the live-monitoring block) to note `judge_events.py` next to `events.py`.

- [ ] **Step 2: Full verification (the whole gate)**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy ramcheck/`
Expected: ALL PASS — every test green, lint clean, format clean, mypy strict clean.

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): judge --web command + monitor gotcha"
```

- [ ] **Step 4: Live smoke (manual — report results, do not auto-merge)**

Pick an eval bundle, remove its judgements so it re-judges fresh, and run with `--web`:

```bash
# bundle from the handoff (gitignored, already evaluated):
cp -r runs/2026-06-20_104844_eval_ndassist /tmp/judgesmoke
rm -f /tmp/judgesmoke/judgements.jsonl /tmp/judgesmoke/judge_events.jsonl
uv run ramcheck judge --bundle /tmp/judgesmoke --judge-config judge.yaml --web
```

Verify in the browser: progress counts up, histogram + red-flag + Ø-score fill in live, the
master-scorecard preview appears at the end, Ctrl-C shuts the monitor down cleanly (no zombie
process — check `pgrep -f ramcheck.webmon` returns nothing after). Report what you observed.

---

## Self-Review

**Spec coverage:**
- J1 (master preview) → Tasks 2/3 (`masters` in `build_view` + HTML), Task 8 (`_master_rows`), Task 9 (`write_masters`). ✓
- J2 (generalise webmon, `--view`) → Tasks 4/5. ✓
- J3 (`judge_events.jsonl` append-only) → Tasks 1/8/9. ✓
- J4 (additive callbacks, default byte-identical) → Task 7 refactor + Task 9 branch + backward-compat test. ✓
- J5 (no load panel) → `TAILS_RESOURCES=False` (Tasks 1/4), asserted in Tasks 3/5. ✓
- J6 (master math in host) → `_master_rows` (Task 8), pre-rendered `master` event. ✓
- J7/J8 (read-only, console stays) → Task 9 keeps console prints, no abort/history. ✓
- Error handling (§7): SSE except clauses preserved (Task 4), `judge_done` in `finally` (Tasks 8/9), defensive `parse_line` (Task 1). ✓

**Placeholder scan:** No TBD/TODO. The one conditional is the Task 9 fixture note — it gives the exact shape and an explicit instruction to reuse an existing bundle helper if present (grep first). All code steps show real code. ✓

**Type consistency:** view interface (`INDEX_HTML`, `TAILS_RESOURCES`, `parse_line`, `build_view().as_dict()`) uniform across `events.py` + `judge_events.py`. `_judge_event_writers` returns 4 callables consumed exactly in Task 9. `_master_rows` row dict keys (`model/variant/pct/safety_passed/safety_reason/recommendation`) match `master_event` params (Task 1) and `write_masters` reads (Task 8). `VerdictView`/`MasterRow`/`JudgeRunView` names consistent. ✓

**Note on `ModelReport` import:** Tasks 7/8 require `ModelReport` imported in `cli.py` — flagged inline in Task 7 Step 1.
