# Web Live-Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only browser live-monitor for `ramcheck eval --web` showing progress X/N, ETA, live host-load (RAM/pressure/throttle) and per-cell pass/fail+latency.

**Architecture:** A separate monitor subprocess (mirrors `_SamplerProcess`) tails an append-only `events.jsonl` (fed by additive `on_run_start`/`on_cell_start`/`on_cell_done` callbacks on `run_eval`) plus the existing `resources.jsonl`, and serves SSE from stdlib `http.server`. The measurement process is never touched. Zero new dependencies.

**Tech Stack:** Python 3.12, stdlib only (`http.server`, `subprocess`, `webbrowser`, `json`), pytest, ruff, mypy strict. Spec: `docs/superpowers/specs/2026-06-20-web-live-monitor-design.md`.

---

## File Structure

- **Create** `ramcheck/events.py` — event contract (constructors + `parse_line`) + view-model aggregation (`build_view` → `RunView`). Pure.
- **Create** `ramcheck/tail.py` — `read_new(path, offset)` byte-offset tail tolerant of missing/partial files. Pure.
- **Create** `ramcheck/loadview.py` — `latest_load(path)` → latest host-load from `resources.jsonl`. Pure.
- **Create** `ramcheck/webmon.py` — the monitor subprocess: `make_handler(bundle)`, SSE loop, `main()`. stdlib `http.server`.
- **Modify** `ramcheck/qualrun.py` — add 3 optional callbacks to `run_eval` and fire them.
- **Modify** `ramcheck/runner.py` — add `_WebMonitorProcess` (copy `_SamplerProcess`, no thread fallback).
- **Modify** `ramcheck/cli.py` — `eval_cmd`: `--web`/`--port`/`--no-open` + `_eval_event_writers` wiring.
- **Modify** `AGENTS.md` — amend the "Markdown+CSV, no HTML" rule + document the new pieces.
- **Create** tests: `tests/test_events.py`, `tests/test_tail.py`, `tests/test_loadview.py`, `tests/test_webmon.py`, `tests/test_eval_web.py`; extend `tests/test_qualrun.py`.

All callbacks default to `None` → `run_eval` behaviour is byte-for-byte unchanged → the 101 existing tests stay green.

---

## Task 1: events.py — event constructors + tolerant parse

**Files:**
- Create: `ramcheck/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_events.py
from ramcheck import events as ev


def test_constructors_have_type_tags():
    assert ev.run_start_event(1.0, 5)["type"] == "run_start"
    assert ev.run_start_event(1.0, 5)["total"] == 5
    cs = ev.cell_start_event(2.0, 0, "m", "v", "A", "p", 0)
    assert cs["type"] == "cell_start" and cs["model"] == "m" and cs["prompt_id"] == "p"
    cd = ev.cell_done_event(3.0, 0, "m", "v", "p", 0, True, 0.3, 1.2, 5.0, 7, False, "")
    assert cd["type"] == "cell_done" and cd["ok"] is True and cd["e2e_s"] == 1.2
    assert ev.run_done_event(4.0, 5, 4)["type"] == "run_done"


def test_dumps_roundtrips_through_parse_line():
    line = ev.dumps(ev.run_start_event(1.0, 3))
    assert ev.parse_line(line) == {"ts": 1.0, "type": "run_start", "total": 3}


def test_parse_line_tolerates_garbage():
    assert ev.parse_line("") is None
    assert ev.parse_line("   ") is None
    assert ev.parse_line('{"truncated": ') is None   # half-written line
    assert ev.parse_line("[1,2,3]") is None           # not a dict
    assert ev.parse_line('{"no":"type"}') is None     # missing type tag
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_events.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ramcheck.events'`

- [ ] **Step 3: Write minimal implementation**

```python
# ramcheck/events.py
"""Event contract for the live monitor.

The eval loop fires callbacks (run_eval's on_run_start/on_cell_start/on_cell_done);
the CLI's --web wiring turns them into lines of an append-only events.jsonl. The
monitor subprocess reads those lines back and aggregates them with build_view().
Pure: no I/O beyond (de)serialising dicts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

RUN_START = "run_start"
CELL_START = "cell_start"
CELL_DONE = "cell_done"
RUN_DONE = "run_done"


def run_start_event(ts: float, total: int) -> dict[str, object]:
    return {"ts": ts, "type": RUN_START, "total": total}


def cell_start_event(
    ts: float, i: int, model: str, variant: str, category: str, prompt_id: str, repeat: int
) -> dict[str, object]:
    return {
        "ts": ts, "type": CELL_START, "i": i, "model": model, "variant": variant,
        "category": category, "prompt_id": prompt_id, "repeat": repeat,
    }


def cell_done_event(
    ts: float, i: int, model: str, variant: str, prompt_id: str, repeat: int, ok: bool,
    ttft_s: float, e2e_s: float, decode_tps: float, completion_tokens: int,
    content_empty: bool, error: str,
) -> dict[str, object]:
    return {
        "ts": ts, "type": CELL_DONE, "i": i, "model": model, "variant": variant,
        "prompt_id": prompt_id, "repeat": repeat, "ok": ok, "ttft_s": ttft_s,
        "e2e_s": e2e_s, "decode_tps": decode_tps, "completion_tokens": completion_tokens,
        "content_empty": content_empty, "error": error,
    }


def run_done_event(ts: float, total: int, ok: int) -> dict[str, object]:
    return {"ts": ts, "type": RUN_DONE, "total": total, "ok": ok}


def dumps(event: dict[str, object]) -> str:
    return json.dumps(event, ensure_ascii=False)


def parse_line(line: str) -> dict[str, object] | None:
    """Parse one events.jsonl line; return None on empty/partial/malformed lines."""
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

Run: `uv run pytest tests/test_events.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/events.py tests/test_events.py
git commit -m "feat(web): events.jsonl contract (constructors + tolerant parse)"
```

---

## Task 2: events.py — build_view aggregation (progress/ETA/cells, resume dedup)

**Files:**
- Modify: `ramcheck/events.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_events.py

def test_build_view_empty():
    v = ev.build_view([])
    assert v.total == 0 and v.done == 0 and v.eta_s is None and v.finished is False


def test_build_view_progress_ok_fail_and_running():
    events = [
        ev.run_start_event(0.0, 4),
        ev.cell_start_event(0.1, 0, "m", "v", "A", "p1", 0),
        ev.cell_done_event(0.2, 0, "m", "v", "p1", 0, True, 0.3, 10.0, 5.0, 7, False, ""),
        ev.cell_done_event(0.3, 1, "m", "v", "p2", 0, False, 0.0, 10.0, 0.0, 0, False, "boom"),
        ev.cell_start_event(0.4, 2, "m", "v", "A", "p3", 0),  # still running
    ]
    v = ev.build_view(events)
    assert v.total == 4
    assert v.done == 2 and v.ok == 1 and v.failed == 1
    assert len(v.running) == 1 and v.running[0].prompt_id == "p3"
    # ETA = mean(e2e of done) * remaining = ((10+10)/2) * (4-2) = 20
    assert v.eta_s == 20.0


def test_build_view_dedups_by_cell_key_for_resume():
    # a cell that ran twice across a crash+resume must count once (last wins)
    key_events = [
        ev.run_start_event(0.0, 1),
        ev.cell_start_event(0.1, 0, "m", "v", "A", "p1", 0),
        ev.cell_done_event(0.2, 0, "m", "v", "p1", 0, False, 0.3, 9.0, 5.0, 7, False, "x"),
        ev.run_start_event(1.0, 1),  # resume writes a fresh run_start
        ev.cell_done_event(1.2, 0, "m", "v", "p1", 0, True, 0.3, 9.0, 5.0, 7, False, ""),
    ]
    v = ev.build_view(key_events)
    assert v.done == 1 and v.ok == 1 and v.failed == 0  # not 2


def test_build_view_finished_on_run_done():
    v = ev.build_view([ev.run_start_event(0.0, 1), ev.run_done_event(1.0, 1, 1)])
    assert v.finished is True


def test_run_view_as_dict_is_json_safe():
    import json
    v = ev.build_view([
        ev.run_start_event(0.0, 1),
        ev.cell_done_event(0.2, 0, "m", "v", "p1", 0, True, 0.3, 9.0, 5.0, 7, False, ""),
    ])
    json.dumps(v.as_dict())  # must not raise
    assert v.as_dict()["cells"][0]["key"] == ["m", "v", "p1", 0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_events.py -q`
Expected: FAIL with `AttributeError: module 'ramcheck.events' has no attribute 'build_view'`

- [ ] **Step 3: Write minimal implementation**

Append to `ramcheck/events.py`:

```python
from dataclasses import dataclass, field

CellKey = tuple[str, str, str, int]


@dataclass
class CellView:
    key: CellKey
    i: int
    model: str
    variant: str
    prompt_id: str
    status: str  # "running" | "done"
    ok: bool | None = None
    ttft_s: float | None = None
    e2e_s: float | None = None
    decode_tps: float | None = None
    content_empty: bool | None = None
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "key": list(self.key), "i": self.i, "model": self.model, "variant": self.variant,
            "prompt_id": self.prompt_id, "status": self.status, "ok": self.ok,
            "ttft_s": self.ttft_s, "e2e_s": self.e2e_s, "decode_tps": self.decode_tps,
            "content_empty": self.content_empty, "error": self.error,
        }
        return d


@dataclass
class RunView:
    total: int = 0
    done: int = 0
    ok: int = 0
    failed: int = 0
    running: list[CellView] = field(default_factory=list)
    cells: list[CellView] = field(default_factory=list)
    eta_s: float | None = None
    finished: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total, "done": self.done, "ok": self.ok, "failed": self.failed,
            "eta_s": self.eta_s, "finished": self.finished,
            "running": [c.as_dict() for c in self.running],
            "cells": [c.as_dict() for c in self.cells],
        }


def _key(ev: dict[str, object]) -> CellKey:
    return (str(ev["model"]), str(ev["variant"]), str(ev["prompt_id"]), int(ev["repeat"]))  # type: ignore[arg-type]


def build_view(events: Iterable[dict[str, object]]) -> RunView:
    """Fold an event stream into a render-ready view. Dedup by cell key (last wins),
    so a cell that re-ran across a crash+resume counts once."""
    total = 0
    finished = False
    by_key: dict[CellKey, CellView] = {}
    order: list[CellKey] = []
    for e in events:
        t = e.get("type")
        if t == RUN_START:
            total = max(total, int(e.get("total", 0)))  # type: ignore[arg-type]
        elif t == RUN_DONE:
            finished = True
        elif t == CELL_START:
            k = _key(e)
            if k not in by_key:
                order.append(k)
            if by_key.get(k) is None or by_key[k].status != "done":
                by_key[k] = CellView(
                    key=k, i=int(e.get("i", -1)), model=str(e["model"]),  # type: ignore[arg-type]
                    variant=str(e["variant"]), prompt_id=str(e["prompt_id"]), status="running",
                )
        elif t == CELL_DONE:
            k = _key(e)
            if k not in by_key:
                order.append(k)
            by_key[k] = CellView(
                key=k, i=int(e.get("i", -1)), model=str(e["model"]),  # type: ignore[arg-type]
                variant=str(e["variant"]), prompt_id=str(e["prompt_id"]), status="done",
                ok=bool(e.get("ok")), ttft_s=_optf(e.get("ttft_s")), e2e_s=_optf(e.get("e2e_s")),
                decode_tps=_optf(e.get("decode_tps")), content_empty=_optb(e.get("content_empty")),
                error=str(e.get("error", "")),
            )
    cells = [by_key[k] for k in order]
    done_cells = [c for c in cells if c.status == "done"]
    done = len(done_cells)
    ok = sum(1 for c in done_cells if c.ok)
    running = [c for c in cells if c.status == "running"]
    e2es = [c.e2e_s for c in done_cells if c.e2e_s is not None]
    eta_s = (sum(e2es) / len(e2es)) * (total - done) if (e2es and total > done) else None
    return RunView(
        total=total, done=done, ok=ok, failed=done - ok, running=running,
        cells=cells, eta_s=eta_s, finished=finished,
    )


def _optf(x: object) -> float | None:
    return float(x) if isinstance(x, (int, float)) else None


def _optb(x: object) -> bool | None:
    return bool(x) if isinstance(x, bool) else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_events.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/events.py tests/test_events.py
git commit -m "feat(web): build_view aggregation (progress/ETA/resume-dedup)"
```

---

## Task 3: tail.py — byte-offset tail tolerant of missing/partial

**Files:**
- Create: `ramcheck/tail.py`
- Test: `tests/test_tail.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tail.py
from ramcheck.tail import read_new


def test_missing_file_yields_nothing(tmp_path):
    lines, off = read_new(tmp_path / "nope.jsonl", 0)
    assert lines == [] and off == 0


def test_reads_complete_lines_and_advances_offset(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("a\nb\n", encoding="utf-8")
    lines, off = read_new(p, 0)
    assert lines == ["a", "b"] and off == 4


def test_partial_last_line_is_not_consumed_until_complete(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("a\nb", encoding="utf-8")          # 'b' has no newline yet
    lines, off = read_new(p, 0)
    assert lines == ["a"] and off == 2              # only 'a\n' consumed
    with p.open("a", encoding="utf-8") as fh:
        fh.write("b\n")                             # complete the line
    lines2, off2 = read_new(p, off)
    assert lines2 == ["b"] and off2 == 6


def test_second_call_returns_only_new(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("a\n", encoding="utf-8")
    _, off = read_new(p, 0)
    with p.open("a", encoding="utf-8") as fh:
        fh.write("c\n")
    lines, _ = read_new(p, off)
    assert lines == ["c"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tail.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ramcheck.tail'`

- [ ] **Step 3: Write minimal implementation**

```python
# ramcheck/tail.py
"""Tail an append-only text file by byte offset, tolerant of a not-yet-existing file
and a partial (still-being-written) last line."""

from __future__ import annotations

from pathlib import Path


def read_new(path: str | Path, offset: int) -> tuple[list[str], int]:
    """Return (complete new lines since `offset`, new byte offset).

    A missing file yields ([], offset). A partial last line (no trailing newline) is
    left unconsumed so it is re-read once finished. Works in bytes so the offset stays
    exact regardless of multi-byte UTF-8.
    """
    p = Path(path)
    if not p.exists():
        return [], offset
    data = p.read_bytes()
    if offset > len(data):  # file was truncated/rotated → restart from the top
        offset = 0
    chunk = data[offset:]
    nl = chunk.rfind(b"\n")
    if nl == -1:
        return [], offset  # nothing complete yet
    consumed = chunk[: nl + 1]
    text = consumed.decode("utf-8", errors="replace")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return lines, offset + len(consumed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tail.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/tail.py tests/test_tail.py
git commit -m "feat(web): byte-offset tail tolerant of missing/partial files"
```

---

## Task 4: loadview.py — latest host-load from resources.jsonl

**Files:**
- Create: `ramcheck/loadview.py`
- Test: `tests/test_loadview.py`

> **Before implementing:** confirm `ResourceSample` field names by reading `ramcheck/models.py` (expected: `sys_used_mb`, `mem_pressure_level`, `throttled`). If they differ, use the real names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_loadview.py
import json

from ramcheck.loadview import latest_load


def _write(p, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_missing_or_empty_returns_none(tmp_path):
    assert latest_load(tmp_path / "nope.jsonl") is None
    p = tmp_path / "r.jsonl"
    p.write_text("", encoding="utf-8")
    assert latest_load(p) is None


def test_returns_latest_sample_and_throttle_history(tmp_path):
    p = tmp_path / "r.jsonl"
    _write(p, [
        {"ts": 1.0, "sys_used_mb": 1000.0, "mem_pressure_level": "normal", "throttled": False},
        {"ts": 2.0, "sys_used_mb": 2000.0, "mem_pressure_level": "warn", "throttled": True},
        {"ts": 3.0, "sys_used_mb": 1500.0, "mem_pressure_level": "normal", "throttled": False},
    ])
    lv = latest_load(p)
    assert lv is not None
    assert lv.sys_used_mb == 1500.0 and lv.mem_pressure == "normal"
    assert lv.throttled is False and lv.any_throttle_seen is True  # throttled earlier


def test_tolerates_partial_last_line(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(
        json.dumps({"ts": 1.0, "sys_used_mb": 9.0, "mem_pressure_level": "x", "throttled": False})
        + "\n" + '{"ts": 2.0, "sys',  # half-written
        encoding="utf-8",
    )
    lv = latest_load(p)
    assert lv is not None and lv.sys_used_mb == 9.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_loadview.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ramcheck.loadview'`

- [ ] **Step 3: Write minimal implementation**

```python
# ramcheck/loadview.py
"""Latest host-load snapshot from the sampler's resources.jsonl, for the live monitor.

The only live source of RAM / memory-pressure / throttle during a run (per-cell
resources don't exist until finalize). `any_throttle_seen` lets the UI render an
honest "n/v" instead of "no throttle" when powermetrics sudo was never available.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LoadView:
    sys_used_mb: float | None
    mem_pressure: str
    throttled: bool
    any_throttle_seen: bool


def latest_load(path: str | Path) -> LoadView | None:
    p = Path(path)
    if not p.exists():
        return None
    last: dict[str, object] | None = None
    any_throttle = False
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        last = d
        if d.get("throttled"):
            any_throttle = True
    if last is None:
        return None
    used = last.get("sys_used_mb")
    return LoadView(
        sys_used_mb=float(used) if isinstance(used, (int, float)) else None,
        mem_pressure=str(last.get("mem_pressure_level", "")),
        throttled=bool(last.get("throttled")),
        any_throttle_seen=any_throttle,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_loadview.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/loadview.py tests/test_loadview.py
git commit -m "feat(web): latest host-load view from resources.jsonl"
```

---

## Task 5: qualrun.py — three optional callbacks on run_eval

**Files:**
- Modify: `ramcheck/qualrun.py` (signature ~line 69-78; `cells` at line 107; loop at 116; flush at 165)
- Test: `tests/test_qualrun.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_qualrun.py

def test_run_eval_fires_progress_callbacks(tmp_path):
    starts, cells_started, cells_done = [], [], []
    run_eval(
        _config(), _pack(), FakeClient(text="hi"), run_dir=tmp_path, sampler=NoopSampler(),
        on_run_start=lambda total: starts.append(total),
        on_cell_start=lambda i, cell: cells_started.append((i, cell.prompt.id)),
        on_cell_done=lambda i, resp: cells_done.append((i, resp.prompt_id, resp.ok)),
    )
    assert starts == [6]                       # total reported once, before the loop
    assert len(cells_started) == 6
    assert len(cells_done) == 6
    assert all(ok for _, _, ok in cells_done)
    assert [i for i, _ in cells_started] == [0, 1, 2, 3, 4, 5]  # enumerate index


def test_run_eval_callbacks_skip_resumed_cells(tmp_path):
    cfg, pack = _config(), _pack()
    run_eval(cfg, pack, FakeClient(), run_dir=tmp_path, sampler=NoopSampler())  # all 6 done
    started = []
    run_eval(
        cfg, pack, FakeClient(), run_dir=tmp_path, sampler=NoopSampler(), resume=True,
        on_cell_start=lambda i, cell: started.append(i),
    )
    assert started == []  # every cell already done → no cell_start fired
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_qualrun.py -q`
Expected: FAIL with `TypeError: run_eval() got an unexpected keyword argument 'on_run_start'`

- [ ] **Step 3: Write minimal implementation**

In `ramcheck/qualrun.py`, add the import near the top (after `from pathlib import Path`):

```python
from collections.abc import Callable
```

Change the `run_eval` signature (keep all existing params, add three):

```python
def run_eval(
    config: Config,
    pack: Pack,
    client: StreamClient,
    *,
    run_dir: str | Path,
    sampler: Sampler | None = None,
    settle_s: float = 0.0,
    resume: bool = False,
    on_run_start: Callable[[int], None] | None = None,
    on_cell_start: Callable[[int, EvalCell], None] | None = None,
    on_cell_done: Callable[[int, EvalResponse], None] | None = None,
) -> list[EvalResponse]:
```

After `cells = iter_eval_cells(config, pack)` (line 107), add:

```python
    if on_run_start is not None:
        on_run_start(len(cells))
```

Change the loop header from `for cell in cells:` to:

```python
            for i, cell in enumerate(cells):
```

Right after the skip-check `continue` block, before `outcome = stream_once(`:

```python
                if on_cell_start is not None:
                    on_cell_start(i, cell)
```

After `new.append(resp)` (just after `fh.flush()`), add:

```python
                if on_cell_done is not None:
                    on_cell_done(i, resp)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_qualrun.py -q`
Expected: PASS (all prior tests + 2 new). Confirms default-`None` path is unchanged.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/qualrun.py tests/test_qualrun.py
git commit -m "feat(eval): additive progress callbacks on run_eval"
```

---

## Task 6: webmon.py — the monitor subprocess (HTTP + SSE)

**Files:**
- Create: `ramcheck/webmon.py`
- Test: `tests/test_webmon.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webmon.py
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from ramcheck import webmon


def _bundle(tmp_path):
    (tmp_path / "events.jsonl").write_text(
        json.dumps({"ts": 1.0, "type": "run_start", "total": 2}) + "\n"
        + json.dumps({
            "ts": 2.0, "type": "cell_done", "i": 0, "model": "m", "variant": "v",
            "prompt_id": "p", "repeat": 0, "ok": True, "ttft_s": 0.3, "e2e_s": 1.0,
            "decode_tps": 5.0, "completion_tokens": 3, "content_empty": False, "error": "",
        }) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "resources.jsonl").write_text(
        json.dumps({"ts": 1.5, "sys_used_mb": 1234.0, "mem_pressure_level": "normal",
                    "throttled": False}) + "\n",
        encoding="utf-8",
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), webmon.make_handler(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_index_serves_html_with_eventsource(tmp_path):
    srv = _bundle(tmp_path)
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=3)
        c.request("GET", "/")
        r = c.getresponse()
        body = r.read().decode("utf-8")
        assert r.status == 200
        assert "text/html" in r.getheader("Content-Type", "")
        assert "EventSource" in body
    finally:
        srv.shutdown()


def test_sse_streams_view_and_load(tmp_path):
    srv = _bundle(tmp_path)
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("GET", "/events")
        r = c.getresponse()
        buf = b""
        for _ in range(40):  # poll is ~0.25s; first frame arrives within a tick
            buf += r.read(256)
            if b"event: view" in buf and b"event: load" in buf:
                break
        text = buf.decode("utf-8", errors="replace")
        assert "event: view" in text and "event: load" in text
        # pull the first `view` data payload and check it parsed correctly
        view_line = next(
            ln for blk in text.split("\n\n") if "event: view" in blk
            for ln in blk.split("\n") if ln.startswith("data: ")
        )
        view = json.loads(view_line[len("data: "):])
        assert view["total"] == 2 and view["done"] == 1 and view["ok"] == 1
    finally:
        c.close()
        srv.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webmon.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ramcheck.webmon'`

- [ ] **Step 3: Write minimal implementation**

```python
# ramcheck/webmon.py
"""Live monitor server — a separate process that tails events.jsonl + resources.jsonl
and serves an SSE dashboard. Run as: python -m ramcheck.webmon --bundle <dir> --port <p>.

It prints the bound port on stdout (so the parent can open the browser) and binds to
127.0.0.1 only (local, single-user). It never touches the measurement process — it only
reads the two append-only files the run produces.
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ramcheck import events as events_mod
from ramcheck import loadview as loadview_mod
from ramcheck import tail as tail_mod

POLL_S = 0.25

INDEX_HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>ramcheck monitor</title>
<style>
 body{font-family:system-ui,sans-serif;margin:1.5rem;background:#111;color:#eee}
 h1{font-size:1.1rem} .bar{background:#333;border-radius:4px;height:1.2rem;overflow:hidden}
 .bar>div{background:#3a7;height:100%;width:0;transition:width .3s}
 .grid{display:flex;gap:1.5rem;margin:1rem 0;flex-wrap:wrap}
 .card{background:#1b1b1b;padding:.7rem 1rem;border-radius:6px;min-width:7rem}
 .num{font-size:1.3rem;font-weight:600}
 table{border-collapse:collapse;width:100%;font-size:.85rem;margin-top:.5rem}
 td,th{padding:.25rem .5rem;border-bottom:1px solid #2a2a2a;text-align:left}
 .ok{color:#5c5} .fail{color:#e66} .muted{color:#999}
</style></head>
<body>
<h1>ramcheck — live eval monitor</h1>
<div class="bar"><div id="barfill"></div></div>
<div class="grid">
 <div class="card"><div class="muted">Fortschritt</div><div class="num"><span id="done">0</span>/<span id="total">0</span></div></div>
 <div class="card"><div class="muted">ok / Fehler</div><div class="num"><span class="ok" id="ok">0</span> / <span class="fail" id="failed">0</span></div></div>
 <div class="card"><div class="muted">ETA</div><div class="num" id="eta">–</div></div>
 <div class="card"><div class="muted">RAM</div><div class="num" id="ram">–</div></div>
 <div class="card"><div class="muted">Pressure</div><div class="num" id="pressure">–</div></div>
 <div class="card"><div class="muted">Throttle</div><div class="num" id="throttle">–</div></div>
</div>
<table><thead><tr><th>#</th><th>Modell</th><th>Variante</th><th>Prompt</th><th>Status</th><th>TTFT</th><th>tok/s</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
function fmtEta(s){if(s==null)return '–';s=Math.round(s);return Math.floor(s/60)+'m '+(s%60)+'s';}
const es=new EventSource('/events');
es.addEventListener('view',e=>{const v=JSON.parse(e.data);
 done.textContent=v.done; total.textContent=v.total; ok.textContent=v.ok; failed.textContent=v.failed;
 eta.textContent=v.finished?'fertig':fmtEta(v.eta_s);
 barfill.style.width=(v.total?100*v.done/v.total:0)+'%';
 rows.innerHTML=v.cells.slice().reverse().map(c=>{
  const st=c.status==='done'?(c.ok?'<span class="ok">✓</span>':'<span class="fail">✗</span>'):'<span class="muted">…</span>';
  const tt=c.ttft_s!=null?c.ttft_s.toFixed(2)+'s':''; const dc=c.decode_tps!=null?c.decode_tps.toFixed(1):'';
  return `<tr><td>${c.i}</td><td>${c.model}</td><td>${c.variant}</td><td>${c.prompt_id}</td><td>${st}</td><td>${tt}</td><td>${dc}</td></tr>`;
 }).join('');});
es.addEventListener('load',e=>{const l=JSON.parse(e.data);
 ram.textContent=l.sys_used_mb!=null?Math.round(l.sys_used_mb)+' MB':'–';
 pressure.textContent=l.mem_pressure||'–';
 throttle.textContent=l.throttled?'JA':(l.any_throttle_seen?'nein':'n/v');});
</script></body></html>"""


def make_handler(bundle: str | Path) -> type[BaseHTTPRequestHandler]:
    bundle_dir = Path(bundle)
    events_path = bundle_dir / "events.jsonl"
    resources_path = bundle_dir / "resources.jsonl"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # keep stdout clean (parent reads port)
            pass

        def do_GET(self) -> None:  # noqa: N802 (stdlib name)
            if self.path == "/" or self.path.startswith("/?"):
                self._serve_index()
            elif self.path.startswith("/events"):
                self._serve_sse()
            else:
                self.send_error(404)

        def _serve_index(self) -> None:
            body = INDEX_HTML.encode("utf-8")
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
                        parsed = events_mod.parse_line(ln)
                        if parsed is not None:
                            all_events.append(parsed)
                    view = events_mod.build_view(all_events)
                    self._send("view", json.dumps(view.as_dict()))
                    load = loadview_mod.latest_load(resources_path)
                    if load is not None:
                        self._send("load", json.dumps({
                            "sys_used_mb": load.sys_used_mb, "mem_pressure": load.mem_pressure,
                            "throttled": load.throttled, "any_throttle_seen": load.any_throttle_seen,
                        }))
                    time.sleep(POLL_S)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return  # browser closed the connection

        def _send(self, kind: str, data: str) -> None:
            self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode())
            self.wfile.flush()

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="ramcheck live monitor server")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.bundle))
    print(server.server_address[1], flush=True)  # parent reads this to open the browser
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_webmon.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/webmon.py tests/test_webmon.py
git commit -m "feat(web): stdlib http.server SSE monitor (tails events+resources)"
```

---

## Task 7: runner.py — _WebMonitorProcess (spawn, read port, stop)

**Files:**
- Modify: `ramcheck/runner.py` (add class after `_SamplerProcess`, ~line 319)
- Test: `tests/test_webmon.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_webmon.py
import http.client as _http

from ramcheck.runner import _WebMonitorProcess


def test_webmonitor_process_spawns_and_serves(tmp_path):
    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")
    mon = _WebMonitorProcess(tmp_path, port=0)
    port = mon.start()
    try:
        assert isinstance(port, int) and port > 0
        c = _http.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("GET", "/")
        assert c.getresponse().status == 200
    finally:
        mon.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webmon.py::test_webmonitor_process_spawns_and_serves -q`
Expected: FAIL with `ImportError: cannot import name '_WebMonitorProcess'`

- [ ] **Step 3: Write minimal implementation**

In `ramcheck/runner.py`, after the `_SamplerProcess` class (it ends at line 318), add (`subprocess`, `sys` are already imported for `_SamplerProcess`):

```python
class _WebMonitorProcess:
    """Decoupled live monitor as its own process (mirrors _SamplerProcess).

    No thread fallback on purpose: a monitor that can't spawn must NOT run inside the
    measurement process — that would defeat the decoupling. It just fails to start.
    """

    def __init__(self, bundle: Path, port: int = 0) -> None:
        self.bundle = bundle
        self.port = port
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> int | None:
        """Spawn the monitor; return the bound port (read from its stdout), or None."""
        cmd = [
            sys.executable, "-m", "ramcheck.webmon",
            "--bundle", str(self.bundle), "--port", str(self.port),
        ]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        except Exception:
            return None
        if self._proc.stdout is None:
            return None
        line = self._proc.stdout.readline().decode("utf-8").strip()
        try:
            return int(line)
        except ValueError:
            return None

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
```

Confirm `from pathlib import Path` is already imported in `runner.py` (it is — `_SamplerProcess` uses `Path`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_webmon.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/runner.py tests/test_webmon.py
git commit -m "feat(web): _WebMonitorProcess spawn (mirrors _SamplerProcess, no fallback)"
```

---

## Task 8: cli.py — eval_cmd --web wiring

**Files:**
- Modify: `ramcheck/cli.py` (imports; add `_eval_event_writers`; extend `eval_cmd` at line 159-191)
- Test: `tests/test_eval_web.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_web.py
from types import SimpleNamespace

from ramcheck import events as ev
from ramcheck.cli import _eval_event_writers


def _cell():
    return SimpleNamespace(
        model=SimpleNamespace(id="m"), variant=SimpleNamespace(id="v"),
        category=SimpleNamespace(id="A"), prompt=SimpleNamespace(id="p"), repeat=0,
    )


def _resp():
    return SimpleNamespace(
        model="m", variant="v", prompt_id="p", repeat=0, ok=True, ttft_s=0.3,
        e2e_s=1.2, decode_tps=5.0, completion_tokens=7, content_empty=False, error="",
    )


def test_eval_event_writers_emit_well_formed_events(tmp_path):
    path = tmp_path / "events.jsonl"
    on_run_start, on_cell_start, on_cell_done, run_done = _eval_event_writers(path)
    on_run_start(3)
    on_cell_start(0, _cell())
    on_cell_done(0, _resp())
    run_done([_resp(), _resp()])

    parsed = [ev.parse_line(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    parsed = [p for p in parsed if p is not None]
    assert [p["type"] for p in parsed] == ["run_start", "cell_start", "cell_done", "run_done"]
    assert parsed[0]["total"] == 3
    assert parsed[1]["model"] == "m" and parsed[1]["prompt_id"] == "p"
    assert parsed[2]["ok"] is True and parsed[2]["e2e_s"] == 1.2
    assert parsed[3]["ok"] == 2  # both responses ok
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_web.py -q`
Expected: FAIL with `ImportError: cannot import name '_eval_event_writers' from 'ramcheck.cli'`

- [ ] **Step 3: Write minimal implementation**

In `ramcheck/cli.py`, add to the imports:

```python
import time
import webbrowser
```

and

```python
from ramcheck import events as events_mod
from ramcheck.runner import _WebMonitorProcess, resolve_engine, run_benchmark
```

(merge the `_WebMonitorProcess` into the existing `from ramcheck.runner import ...` line.)

Add this helper above `eval_cmd`:

```python
def _eval_event_writers(events_path: Path):  # type: ignore[no-untyped-def]
    """Closures that translate run_eval's callbacks into events.jsonl lines."""
    fh = events_path.open("a", encoding="utf-8")

    def _w(event: dict[str, object]) -> None:
        fh.write(events_mod.dumps(event) + "\n")
        fh.flush()

    def on_run_start(total: int) -> None:
        _w(events_mod.run_start_event(time.time(), total))

    def on_cell_start(i: int, cell) -> None:  # type: ignore[no-untyped-def]
        _w(events_mod.cell_start_event(
            time.time(), i, cell.model.id, cell.variant.id, cell.category.id,
            cell.prompt.id, cell.repeat,
        ))

    def on_cell_done(i: int, resp) -> None:  # type: ignore[no-untyped-def]
        _w(events_mod.cell_done_event(
            time.time(), i, resp.model, resp.variant, resp.prompt_id, resp.repeat,
            resp.ok, resp.ttft_s, resp.e2e_s, resp.decode_tps, resp.completion_tokens,
            resp.content_empty, resp.error,
        ))

    def run_done(responses) -> None:  # type: ignore[no-untyped-def]
        _w(events_mod.run_done_event(
            time.time(), len(responses), sum(1 for r in responses if r.ok)))
        fh.close()

    return on_run_start, on_cell_start, on_cell_done, run_done
```

Replace the `eval_cmd` body. Add the three options to the signature:

```python
@app.command(name="eval")
def eval_cmd(
    pack: Path = typer.Option(..., "--pack", exists=True, help="use-case pack YAML"),
    config: Path = typer.Option(..., "--config", "-c", exists=True, help="endpoint/models YAML"),
    out: Path | None = typer.Option(None, "--out", help="override output_dir"),
    resume: Path | None = typer.Option(
        None, "--resume", exists=True, help="continue an existing bundle dir (skip done cells)"
    ),
    web: bool = typer.Option(False, "--web", help="live browser monitor for this run"),
    port: int = typer.Option(0, "--port", help="monitor port (0 = auto)"),
    no_open: bool = typer.Option(False, "--no-open", help="don't auto-open the browser"),
) -> None:
    """Run a use-case pack through the models: capture answers + perf, write the bundle."""
    cfg = load_config(config)
    pk = load_pack(pack)
    if resume is not None:
        run_dir = resume
        console.print(f"[bold]ramcheck eval[/] [{pk.id}] → [cyan]{run_dir}[/] [dim](resume)[/]")
    else:
        base_out = out or cfg.output_path()
        run_dir = base_out / f"{_timestamp()}_eval_{pk.id}"
        console.print(f"[bold]ramcheck eval[/] [{pk.id}] → [cyan]{run_dir}[/]")

    client = _make_client(cfg)

    if web:
        run_dir.mkdir(parents=True, exist_ok=True)  # events.jsonl is opened before run_eval
        monitor = _WebMonitorProcess(run_dir, port=port)
        bound = monitor.start()
        if bound:
            url = f"http://127.0.0.1:{bound}"
            console.print(f"[bold]Monitor:[/] [cyan]{url}[/] [dim](Ctrl-C zum Beenden)[/]")
            if not no_open:
                webbrowser.open(url)
        else:
            console.print("[yellow]Web-Monitor konnte nicht starten — Lauf läuft ohne ihn.[/]")
        on_run_start, on_cell_start, on_cell_done, run_done = _eval_event_writers(
            run_dir / "events.jsonl"
        )
        responses: list = []
        try:
            responses = run_eval(
                cfg, pk, client, run_dir=run_dir, resume=resume is not None,
                on_run_start=on_run_start, on_cell_start=on_cell_start, on_cell_done=on_cell_done,
            )
        finally:
            run_done(responses)
            monitor.stop()
    else:
        responses = run_eval(cfg, pk, client, run_dir=run_dir, resume=resume is not None)

    host = hostinfo.summary()
    _write_bundle_manifest(run_dir, pack, cfg, host)

    md = scorecard_mod.render_scorecard_md(pk, responses, [], [], host=host, date_str=_today())
    (run_dir / "scorecard.md").write_text(md, encoding="utf-8")
    errors = sum(1 for r in responses if not r.ok)
    console.print(
        f"[green]✓[/] {len(responses)} Antworten ({errors} Fehler) · "
        f"[bold]{run_dir / 'scorecard.md'}[/] (Tech-Specs gefüllt, Qualität offen) · "
        f"bewerten: [cyan]ramcheck judge --bundle {run_dir} --judge-config judge.yaml[/]"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_web.py -q`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/cli.py tests/test_eval_web.py
git commit -m "feat(cli): eval --web wiring (monitor subprocess + events.jsonl)"
```

---

## Task 9: AGENTS.md amendment + full verification

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Amend the constitution line**

In `AGENTS.md`, change the bullet:

```
- **Output is Markdown + CSV, nothing else.** No DOCX/PDF/HTML.
```

to:

```
- **Persisted output is Markdown + CSV, nothing else.** No DOCX/PDF/HTML *artifacts*.
  (The opt-in `eval --web` live monitor serves HTML transiently for viewing; it persists
  nothing as HTML — the bundle stays jsonl/csv/md.)
```

- [ ] **Step 2: Document the new command + module**

In the `cli.py` line of the architecture block, change:

```
cli.py      typer app: run · embed · report · eval · judge
```

to:

```
cli.py      typer app: run · embed · report · eval [--web] · judge
```

Add to the architecture block (after `scorecard.py`):

```
webmon.py   separate live-monitor process (stdlib http.server + SSE): tails events.jsonl
            + resources.jsonl, serves a read-only dashboard. Spawned like _SamplerProcess.
events.py   events.jsonl contract (append-only) + view-model aggregation for the monitor
tail.py     byte-offset tail tolerant of missing/partial files (used by webmon)
loadview.py latest host-load snapshot from resources.jsonl (used by webmon)
```

Add a Gotcha bullet:

```
- **The live monitor never tails `responses.jsonl`.** It is rewritten wholesale at finalize
  (`qualrun._write_responses`). Live progress comes from the append-only `events.jsonl`
  (fed by `run_eval`'s `on_cell_*` callbacks); live host-load from `resources.jsonl`.
```

- [ ] **Step 3: Run the full suite + lint + types**

Run:
```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy ramcheck/
```
Expected: all tests pass (101 prior + new), ruff clean, mypy clean.

> If `ruff format --check` reports diffs, run `uv run ruff format .` and re-stage.

- [ ] **Step 4: Manual smoke (optional, needs a live endpoint)**

```bash
uv run ramcheck eval --pack packs/ndassist.yaml --config config.ndeval.yaml --web
# browser opens on 127.0.0.1:<auto>; watch progress/ETA/load/cell rows; Ctrl-C to stop.
```

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): document eval --web monitor + amend output rule"
```

---

## Self-Review checklist (run after writing, before execution)

- **Spec coverage:** events.jsonl (T1-2), tail (T3), loadview (T4), callbacks (T5), webmon/SSE (T6),
  _WebMonitorProcess no-fallback (T7), CLI --web/--port/--no-open + run_done-in-finally (T8),
  AGENTS amendment + verification (T9). Throttle "n/v" → `any_throttle_seen` (T4 + HTML). Snapshot-
  on-connect → SSE loop starts at offset 0 (T6). Resume dedup-by-key → build_view (T2). ✓ all covered.
- **Placeholders:** none — every step has real code/commands.
- **Type consistency:** `run_start_event/cell_start_event/cell_done_event/run_done_event`, `dumps`,
  `parse_line`, `build_view`→`RunView.as_dict`, `read_new`, `latest_load`→`LoadView`,
  `make_handler`, `_WebMonitorProcess.start/stop`, `_eval_event_writers` — names identical across tasks.
- **Out of scope (deferred):** token/thinking stream, `judge --web`, `run --web`, start/abort/history,
  `serve <bundle>`, per-cell live RAM.
