# GUI-Steuerzentrale (`touchstone gui`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent, local benchmarking control-center (`touchstone gui`) as a walking skeleton through all 7 stations (configure → start → watch live → evaluate → compare → export).

**Architecture:** A long-lived FastAPI server (optional `[gui]` extra) that **spawns** `touchstone eval/judge` as subprocesses (out-of-process control-plane), tails their `events.jsonl` for live progress, and reuses the existing pure functions (`load_pack`, `scorecard`, `aggregate`, `build_view`). `runs/` stays the SSOT; the only new state is a transient **Run-Sentinel** (`run.json`) that triples as run_dir-handle, cross-process one-run lock, and discovery anchor.

**Tech Stack:** Python 3.12 · FastAPI/Starlette · uvicorn · Jinja2 · HTMX/Alpine (vendored) · pytest/mypy/ruff. Spec: [`docs/superpowers/specs/2026-06-21-gui-steuerzentrale-design.md`](../specs/2026-06-21-gui-steuerzentrale-design.md).

---

## File Structure

| File | Responsibility |
|---|---|
| `touchstone/cli.py` (modify) | add `--run-dir`/`--emit-events` to `eval`; decouple event-writers from `_live_monitor`; add lazy `gui` command; move `_master_rows` out |
| `touchstone/scorecard.py` (modify) | host the now-public `master_rows()` helper (shared by CLI + GUI) |
| `touchstone/gui/__init__.py` (new) | package marker |
| `touchstone/gui/control.py` (new) | Run-Sentinel I/O + `ProcessLauncher` protocol/impl + `RunRegistry` (one-run lock) |
| `touchstone/gui/bundles.py` (new) | discovery/classification of `runs/` + per-bundle verdict recompute |
| `touchstone/gui/live.py` (new) | tail + `build_view` → live view dicts (SSE source) |
| `touchstone/gui/app.py` (new) | FastAPI factory: the 7 station routes + start/stop/SSE |
| `touchstone/gui/templates/` (new) | Jinja2 app-shell + station fragments |
| `touchstone/gui/static/` (new) | vendored htmx.min.js, alpine.min.js, app.css |
| `pyproject.toml` (modify) | `[gui]` optional-dependency group |
| `tests/test_gui_*.py` (new) | per-module tests (core ones run without FastAPI; route tests skip if `[gui]` missing) |
| `AGENTS.md` (modify) | `touchstone gui` command + architecture notes |

Tasks are ordered so the **greenfield core (Sentinel + control-plane)** lands first, then discovery/live, then the FastAPI surface, then the front-end, then wiring + smoke.

---

## Task 1: `eval --run-dir` — host-side exact run-dir

**Why:** Today `eval_cmd` invents `base_out / f"{ts}_eval_{pk.id}"` itself; the GUI must know the run_dir at spawn time to tail it. Add an option that pins the exact dir. (`judge` already takes `--bundle` as its dir — no change needed there.)

**Files:**
- Modify: `touchstone/cli.py:510-531` (eval_cmd signature + run_dir resolution)
- Test: `tests/test_gui_cli_runargs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_cli_runargs.py
from typer.testing import CliRunner

from touchstone.cli import app

runner = CliRunner()


def test_eval_run_dir_option_pins_exact_dir(tmp_path, monkeypatch):
    """--run-dir makes eval use exactly that dir (no timestamp suffix appended)."""
    captured = {}

    def fake_run_eval(cfg, pk, client, *, run_dir, resume=False, **cb):
        captured["run_dir"] = run_dir
        return []

    monkeypatch.setattr("touchstone.cli.run_eval", fake_run_eval)
    monkeypatch.setattr("touchstone.cli._finalize_eval_bundle", lambda *a, **k: None)
    monkeypatch.setattr("touchstone.cli._make_client", lambda cfg: object())

    target = tmp_path / "my_exact_run"
    res = runner.invoke(
        app,
        ["eval", "--pack", "packs/ndassist.yaml", "--config", "config.example.yaml",
         "--run-dir", str(target)],
    )
    assert res.exit_code == 0, res.output
    assert captured["run_dir"] == target
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_cli_runargs.py::test_eval_run_dir_option_pins_exact_dir -v`
Expected: FAIL — `eval` has no `--run-dir` option (exit_code != 0 / unexpected option).

- [ ] **Step 3: Implement the option**

In `eval_cmd` signature (after `out`), add:

```python
    run_dir_opt: Path | None = typer.Option(
        None, "--run-dir", help="use this exact run dir (GUI control-plane); overrides --out"
    ),
```

Replace the run_dir resolution block (`cli.py:525-531`) with:

```python
    if resume is not None:
        run_dir = resume
        console.print(f"[bold]touchstone eval[/] [{pk.id}] → [cyan]{run_dir}[/] [dim](resume)[/]")
    elif run_dir_opt is not None:
        run_dir = run_dir_opt
        console.print(f"[bold]touchstone eval[/] [{pk.id}] → [cyan]{run_dir}[/]")
    else:
        base_out = out or cfg.output_path()
        run_dir = base_out / f"{_timestamp()}_eval_{pk.id}"
        console.print(f"[bold]touchstone eval[/] [{pk.id}] → [cyan]{run_dir}[/]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_cli_runargs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add touchstone/cli.py tests/test_gui_cli_runargs.py
git commit -m "feat(cli): eval --run-dir for host-pinned run dir (GUI control-plane)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `eval --emit-events` — event-writers without the monitor

**Why:** The GUI tails `events.jsonl` itself (no second HTTP server). Today the event-writers are lexically fused inside `with _live_monitor(...)`. Decouple them so `--emit-events` writes events **without** spawning webmon, and use **truncate** mode for that path (fixes the `finished=True` latch on resume). The no-flag default path stays byte-identical.

**Files:**
- Modify: `touchstone/cli.py` — `_eval_event_writers` (add `append` param), `eval_cmd` (`--emit-events`, restructured emit path)
- Test: `tests/test_gui_cli_runargs.py` (extend), reuse `tests/test_eval_web.py` as the byte-identity guard

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_cli_runargs.py`:

```python
from types import SimpleNamespace

from touchstone.cli import _eval_event_writers


def test_eval_event_writers_truncate_mode_overwrites(tmp_path):
    """append=False truncates so each spawn starts a fresh stream (no stale run_done)."""
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"run_done","ts":1,"total":9,"ok":9}\n', encoding="utf-8")
    on_run_start, _, _, run_done = _eval_event_writers(path, append=False)
    on_run_start(2)
    text = path.read_text(encoding="utf-8")
    assert "run_done" not in text  # old line gone
    assert '"run_start"' in text and '"total": 2' in text


def test_eval_emit_events_writes_events_without_monitor(tmp_path, monkeypatch):
    """--emit-events writes events.jsonl but never spawns _live_monitor."""
    spawned = {"monitor": False}
    monkeypatch.setattr(
        "touchstone.cli._live_monitor",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("monitor must not spawn")),
    )

    def fake_run_eval(cfg, pk, client, *, run_dir, resume=False,
                      on_run_start=None, on_cell_start=None, on_cell_done=None):
        if on_run_start:
            on_run_start(1)
        return []

    monkeypatch.setattr("touchstone.cli.run_eval", fake_run_eval)
    monkeypatch.setattr("touchstone.cli._finalize_eval_bundle", lambda *a, **k: None)
    monkeypatch.setattr("touchstone.cli._make_client", lambda cfg: object())

    target = tmp_path / "run1"
    res = runner.invoke(
        app,
        ["eval", "--pack", "packs/ndassist.yaml", "--config", "config.example.yaml",
         "--run-dir", str(target), "--emit-events"],
    )
    assert res.exit_code == 0, res.output
    assert (target / "events.jsonl").exists()
    assert not spawned["monitor"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_cli_runargs.py -v`
Expected: FAIL — `_eval_event_writers()` takes no `append` kwarg; `--emit-events` unknown.

- [ ] **Step 3: Implement `append` param on the writer**

In `_eval_event_writers` change the signature + open mode (`cli.py:243,252`):

```python
def _eval_event_writers(
    events_path: Path,
    *,
    append: bool = True,
) -> tuple[
    Callable[[int], None],
    Callable[[int, EvalCell], None],
    Callable[[int, EvalResponse], None],
    Callable[[list[EvalResponse]], None],
]:
    """Closures that translate run_eval's callbacks into events.jsonl lines.

    append=True keeps the historical --web semantics; append=False truncates so a
    GUI-spawned run starts a fresh stream (no stale run_done → no false 'finished')."""
    fh = events_path.open("a" if append else "w", encoding="utf-8")
```

- [ ] **Step 4: Restructure `eval_cmd`'s emit path**

Add to the signature (after `resume`):

```python
    emit_events: bool = typer.Option(
        False, "--emit-events", help="write events.jsonl without spawning the monitor (GUI)"
    ),
```

Replace the body from `client = _make_client(cfg)` to the end of `eval_cmd` (`cli.py:533-559`) with:

```python
    client = _make_client(cfg)
    emit = web or emit_events
    if not emit:
        responses = run_eval(cfg, pk, client, run_dir=run_dir, resume=resume is not None)
        _finalize_eval_bundle(run_dir, pack, cfg, pk, responses)
        return

    run_dir.mkdir(parents=True, exist_ok=True)  # events.jsonl is opened before run_eval
    monitor_cm = (
        _live_monitor(run_dir, port, no_open)
        if web
        else contextlib.nullcontext((None, None))
    )
    with monitor_cm as (monitor, url):
        # --web keeps append (resume semantics); GUI --emit-events truncates (fresh stream).
        on_run_start, on_cell_start, on_cell_done, run_done = _eval_event_writers(
            run_dir / "events.jsonl", append=web
        )
        responses = []
        try:
            responses = run_eval(
                cfg,
                pk,
                client,
                run_dir=run_dir,
                resume=resume is not None,
                on_run_start=on_run_start,
                on_cell_start=on_cell_start,
                on_cell_done=on_cell_done,
            )
        finally:
            run_done(responses)
        _finalize_eval_bundle(run_dir, pack, cfg, pk, responses)
        if web and monitor is not None:
            _hold_monitor(monitor, url)
```

- [ ] **Step 5: Run the new tests + the byte-identity guard**

Run: `uv run pytest tests/test_gui_cli_runargs.py tests/test_eval_web.py -v`
Expected: PASS — new behavior works AND the existing `--web` writer tests still pass (byte-identity of the writer closures preserved).

- [ ] **Step 6: Full suite + types**

Run: `uv run pytest -q && uv run mypy touchstone/`
Expected: all green (no regression in the no-flag default path).

- [ ] **Step 7: Commit**

```bash
git add touchstone/cli.py tests/test_gui_cli_runargs.py
git commit -m "feat(cli): eval --emit-events (writers without monitor) + truncate-per-spawn

Decouples the event-writers from the _live_monitor context so the GUI can tail
events.jsonl without a second HTTP server. --web keeps append; --emit-events
truncates (fixes the finished=True latch on resume). Default path byte-identical.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Move `master_rows` into `scorecard.py` (shared by CLI + GUI)

**Why:** Both Station 1 (overview verdict badge) and Station 5 (result view) recompute the per-(model,variant) verdict. Lift `cli._master_rows` to a public `scorecard.master_rows` so GUI and CLI share one implementation (no duplicate scoring).

**Files:**
- Modify: `touchstone/scorecard.py` (add `master_rows`), `touchstone/cli.py` (import + delete local, keep `_master_rows` as thin alias for back-compat of the judge path)
- Test: `tests/test_gui_master_rows.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_master_rows.py
from touchstone import scorecard
from touchstone.pack import load_pack
from touchstone.results import EvalResponse, ModelReport, Verdict


def _resp(model="m", variant="baseline"):
    return EvalResponse(
        pack_id="ndassist", pack_version=1, machine="t", model=model, quant="q",
        engine="e", engine_version="x", variant=variant, category="A", prompt_id="A1",
        repeat=0, response_text="ok", content_empty=False, ttft_s=0.1, decode_tps=1.0,
        prefill_tps=1.0, e2e_s=1.0, prompt_tokens=1, completion_tokens=1, is_cold_start=False,
        power_source="ac", peak_rss_mb=0.0, sys_used_mb=0.0, mem_pressure_max="normal",
        throttled=False, ok=True, error="", seed=42, t_start=0.0, t_end=1.0, reasoning_chars=0,
    )


def test_master_rows_public_matches_recommendation():
    pk = load_pack("packs/ndassist.yaml")
    responses = [_resp()]
    verdicts = [Verdict("m", "baseline", "A1", 0, "A", 5, False, "good", False, False)]
    # all master dims = 5 → 100% → recommendation 'Ja', safety passes
    reports = [ModelReport("m", "baseline", {d.id: 5 for d in pk.dimensions}, {})]
    rows = scorecard.master_rows(pk, responses, verdicts, reports)
    assert len(rows) == 1
    assert rows[0]["model"] == "m" and rows[0]["variant"] == "baseline"
    assert rows[0]["recommendation"] == "Ja"
    assert rows[0]["safety_passed"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_master_rows.py -v`
Expected: FAIL — `scorecard.master_rows` does not exist.

- [ ] **Step 3: Add `master_rows` to `scorecard.py`**

Append (it only uses functions already in `scorecard.py`):

```python
def master_rows(
    pk: "Pack",
    responses: list["EvalResponse"],
    verdicts: list["Verdict"],
    reports: list["ModelReport"],
) -> list[dict[str, object]]:
    """Per-(model, variant) master summary (pct, safety, recommendation), computed in the
    host process so any consumer (judge monitor, GUI overview/result) matches scorecard.md."""
    reports_by = {(r.model, r.variant): r for r in reports}
    rows: list[dict[str, object]] = []
    for model, variant in model_variant_groups(responses):
        rep = reports_by.get((model, variant))
        if not (rep and rep.dim_scores):
            continue
        _, _, pct = weighted_total(rep.dim_scores, pk)
        gv = [v for v in verdicts if (v.model, v.variant) == (model, variant)]
        passed, reason = passes_ko(rep.dim_scores, red_flagged_prompts(gv), pk)
        rows.append(
            {
                "model": model,
                "variant": variant,
                "pct": pct,
                "safety_passed": passed,
                "safety_reason": reason,
                "recommendation": recommendation(passed, pct),
            }
        )
    return rows
```

Ensure the `TYPE_CHECKING` imports (`Pack`, `EvalResponse`, `Verdict`, `ModelReport`) exist at the top of `scorecard.py`; add any missing under the existing typing guard.

- [ ] **Step 4: Rewire `cli._master_rows` to delegate**

Replace the body of `cli._master_rows` (`cli.py:384-414`) with a one-liner delegate (keeps the judge call sites unchanged):

```python
def _master_rows(
    pk: Pack,
    responses: list[EvalResponse],
    verdicts: list[Verdict],
    reports: list[ModelReport],
) -> list[dict[str, object]]:
    return scorecard_mod.master_rows(pk, responses, verdicts, reports)
```

- [ ] **Step 5: Run tests + types + the judge-monitor regression**

Run: `uv run pytest tests/test_gui_master_rows.py tests/test_cli_judge_web.py -q && uv run mypy touchstone/`
Expected: PASS (judge path unchanged, new helper green).

- [ ] **Step 6: Commit**

```bash
git add touchstone/scorecard.py touchstone/cli.py tests/test_gui_master_rows.py
git commit -m "refactor(scorecard): promote master_rows to public (shared by CLI + GUI)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `[gui]` extra + `touchstone gui` lazy command

**Why:** GUI deps must be optional; core/CLI/CI must run without FastAPI. `touchstone gui` lazily imports the server and prints an install hint if the extra is missing.

**Files:**
- Modify: `pyproject.toml` (`[gui]` group), `touchstone/cli.py` (gui command)
- Create: `touchstone/gui/__init__.py`
- Test: `tests/test_gui_cli_command.py`

- [ ] **Step 1: Add the optional-dependency group**

In `pyproject.toml` under `[project.optional-dependencies]`, after `tokenizer = [...]`:

```toml
# Local control-center web UI (touchstone gui). Build-free: vendored HTMX/Alpine assets.
gui = [
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
]
```

- [ ] **Step 2: Create the package marker**

```python
# touchstone/gui/__init__.py
"""Optional web control-center (touchstone gui). Imported only when the server runs;
the harness core never imports this package."""
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_gui_cli_command.py
from typer.testing import CliRunner

from touchstone.cli import app

runner = CliRunner()


def test_gui_command_without_extra_prints_install_hint(monkeypatch):
    """If FastAPI isn't importable, `touchstone gui` exits 1 with an install hint, no traceback."""
    import builtins

    real_import = builtins.__import__

    def block_gui(name, *a, **k):
        if name.startswith("touchstone.gui.app"):
            raise ImportError("No module named 'fastapi'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", block_gui)
    res = runner.invoke(app, ["gui"])
    assert res.exit_code == 1
    assert "pip install" in res.output and "[gui]" in res.output
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_cli_command.py -v`
Expected: FAIL — no `gui` command.

- [ ] **Step 5: Add the `gui` command to `cli.py`**

```python
@app.command()
def gui(
    runs: Path = typer.Option(
        Path("./runs"), "--runs", help="runs dir to read/write bundles under"
    ),
    port: int = typer.Option(0, "--port", help="server port (0 = auto)"),
    no_open: bool = typer.Option(False, "--no-open", help="don't auto-open the browser"),
) -> None:
    """Launch the local web control-center (requires the [gui] extra)."""
    try:
        from touchstone.gui.app import serve
    except ImportError:
        console.print(
            "[red]GUI-Abhängigkeiten fehlen.[/] Installiere sie mit "
            "[cyan]pip install -e '.[gui]'[/] (oder [cyan]uv sync --extra gui[/])."
        )
        raise typer.Exit(code=1) from None
    serve(runs_dir=runs, port=port, open_browser=not no_open)
```

(`serve` is implemented in Task 11; until then the import resolves but the test only exercises the missing-extra branch.)

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_cli_command.py -v`
Expected: PASS.

- [ ] **Step 7: Install the extra for subsequent tasks + commit**

```bash
uv sync --extra gui
git add pyproject.toml uv.lock touchstone/gui/__init__.py touchstone/cli.py tests/test_gui_cli_command.py
git commit -m "feat(cli): touchstone gui command + optional [gui] extra (lazy import)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `control.py` — Run-Sentinel I/O

**Why:** The sentinel (`run.json` in the run dir) is the linchpin: run_dir handle + cross-process one-run lock + discovery anchor. Pure file I/O + liveness check, fully unit-testable.

**Files:**
- Create: `touchstone/gui/control.py` (sentinel part)
- Test: `tests/test_gui_control_sentinel.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_control_sentinel.py
import os

from touchstone.gui import control


def test_write_read_roundtrip(tmp_path):
    rd = tmp_path / "run1"
    rd.mkdir()
    control.write_sentinel(rd, kind="eval", pid=os.getpid(),
                           pack_path="packs/ndassist.yaml", config_path="config.m5.yaml")
    s = control.read_sentinel(rd)
    assert s is not None
    assert s["kind"] == "eval" and s["pid"] == os.getpid()
    assert s["state"] == "running"


def test_is_active_true_for_live_pid(tmp_path):
    rd = tmp_path / "run2"
    rd.mkdir()
    control.write_sentinel(rd, kind="eval", pid=os.getpid(),
                           pack_path="p", config_path="c")
    assert control.is_active(control.read_sentinel(rd)) is True


def test_is_active_false_for_dead_pid(tmp_path):
    rd = tmp_path / "run3"
    rd.mkdir()
    control.write_sentinel(rd, kind="eval", pid=2_000_000_000,  # almost certainly dead
                           pack_path="p", config_path="c")
    assert control.is_active(control.read_sentinel(rd)) is False


def test_read_missing_returns_none(tmp_path):
    assert control.read_sentinel(tmp_path / "nope") is None


def test_mark_and_clear(tmp_path):
    rd = tmp_path / "run4"
    rd.mkdir()
    control.write_sentinel(rd, kind="judge", pid=os.getpid(), pack_path="p", config_path="c")
    control.mark_sentinel(rd, "finished")
    assert control.read_sentinel(rd)["state"] == "finished"
    control.clear_sentinel(rd)
    assert control.read_sentinel(rd) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_control_sentinel.py -v`
Expected: FAIL — `touchstone.gui.control` does not exist.

- [ ] **Step 3: Implement the sentinel part of `control.py`**

```python
# touchstone/gui/control.py
"""Out-of-process control-plane for the GUI: a run-sentinel (run.json) + a registry that
spawns/stops touchstone measurement subprocesses and enforces one-run-at-a-time.

The sentinel is transient steuer-state, NOT measurement truth (runs/ stays SSOT). It lives
in the active run dir and triples as: (1) the run_dir handle, (2) a cross-process lock that
survives a GUI restart, (3) a discovery anchor for running/crashed runs."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

SENTINEL_NAME = "run.json"


def sentinel_path(run_dir: Path) -> Path:
    return run_dir / SENTINEL_NAME


def write_sentinel(
    run_dir: Path,
    *,
    kind: str,
    pid: int,
    pack_path: str,
    config_path: str,
) -> None:
    """Write run.json BEFORE/at spawn. state starts as 'running'."""
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": kind,  # "eval" | "judge"
        "run_dir": str(run_dir),
        "pid": pid,
        "pack_path": pack_path,
        "config_path": config_path,
        "started_ts": time.time(),
        "state": "running",
    }
    sentinel_path(run_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_sentinel(run_dir: Path) -> dict[str, Any] | None:
    p = sentinel_path(run_dir)
    if not p.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return None


def mark_sentinel(run_dir: Path, state: str) -> None:
    s = read_sentinel(run_dir)
    if s is None:
        return
    s["state"] = state
    sentinel_path(run_dir).write_text(
        json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clear_sentinel(run_dir: Path) -> None:
    sentinel_path(run_dir).unlink(missing_ok=True)


def is_active(sentinel: dict[str, Any] | None) -> bool:
    """True iff the sentinel says running AND its PID is alive."""
    if sentinel is None or sentinel.get("state") != "running":
        return False
    return _pid_alive(int(sentinel["pid"]))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_control_sentinel.py -v`
Expected: PASS.

- [ ] **Step 5: Types + commit**

```bash
uv run mypy touchstone/gui/
git add touchstone/gui/control.py tests/test_gui_control_sentinel.py
git commit -m "feat(gui): run-sentinel (run.json) — run_dir handle + lock + discovery anchor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `control.py` — ProcessLauncher + RunRegistry (one-run lock)

**Why:** The registry chooses the run_dir host-side, writes the sentinel, spawns the subprocess via an injectable `ProcessLauncher` (real = Popen; fake = test), and enforces G8 via the sentinel lock (survives GUI restart).

**Files:**
- Modify: `touchstone/gui/control.py` (add launcher + registry)
- Test: `tests/test_gui_control_registry.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_control_registry.py
import pytest

from touchstone.gui import control


class FakeLauncher:
    """Records argv, simulates a process whose liveness we toggle."""

    def __init__(self):
        self.calls = []
        self._alive = True
        self.pid = 4242
        self.terminated = False

    def spawn(self, argv):
        self.calls.append(argv)
        return self.pid

    def alive(self, pid):
        return self._alive

    def terminate(self, pid):
        self.terminated = True
        self._alive = False


def _reg(tmp_path):
    return control.RunRegistry(runs_dir=tmp_path, launcher=FakeLauncher())


def test_start_eval_writes_sentinel_and_passes_run_dir(tmp_path):
    reg = _reg(tmp_path)
    handle = reg.start_eval(pack_path="packs/ndassist.yaml", config_path="config.m5.yaml")
    argv = reg.launcher.calls[0]
    assert "--run-dir" in argv and str(handle.run_dir) in argv
    assert "--emit-events" in argv and "eval" in argv
    assert control.read_sentinel(handle.run_dir)["kind"] == "eval"


def test_second_start_blocked_by_active_sentinel(tmp_path):
    reg = _reg(tmp_path)
    reg.start_eval(pack_path="p", config_path="c")
    with pytest.raises(control.RunInProgress):
        reg.start_eval(pack_path="p2", config_path="c2")


def test_lock_survives_fresh_registry(tmp_path):
    """A new registry (simulated GUI restart) still sees the on-disk sentinel lock."""
    reg1 = _reg(tmp_path)
    reg1.start_eval(pack_path="p", config_path="c")
    reg2 = control.RunRegistry(runs_dir=tmp_path, launcher=FakeLauncher())
    with pytest.raises(control.RunInProgress):
        reg2.start_eval(pack_path="p", config_path="c")


def test_stale_sentinel_is_reclaimed(tmp_path):
    reg = _reg(tmp_path)
    h = reg.start_eval(pack_path="p", config_path="c")
    reg.launcher._alive = False  # process died without cleanup
    # a new start should now succeed (stale lock reclaimed)
    h2 = reg.start_eval(pack_path="p2", config_path="c2")
    assert h2.run_dir != h.run_dir


def test_poll_maps_exit(tmp_path):
    reg = _reg(tmp_path)
    h = reg.start_eval(pack_path="p", config_path="c")
    assert reg.poll(h) == "running"
    reg.launcher._alive = False
    assert reg.poll(h) in {"finished", "failed"}


def test_stop_terminates_and_marks(tmp_path):
    reg = _reg(tmp_path)
    h = reg.start_eval(pack_path="p", config_path="c")
    reg.stop(h)
    assert reg.launcher.terminated
    assert control.read_sentinel(h.run_dir)["state"] == "stopped"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_control_registry.py -v`
Expected: FAIL — `RunRegistry`, `RunInProgress`, `ProcessLauncher` not defined.

- [ ] **Step 3: Implement launcher + registry**

Append to `touchstone/gui/control.py`:

```python
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


class RunInProgress(RuntimeError):
    """Raised when a start is attempted while a measurement run is already active."""


@dataclass
class RunHandle:
    kind: str          # "eval" | "judge"
    run_dir: Path
    pid: int


@runtime_checkable
class ProcessLauncher(Protocol):
    def spawn(self, argv: list[str]) -> int: ...
    def alive(self, pid: int) -> bool: ...
    def terminate(self, pid: int) -> None: ...


class RealProcessLauncher:
    """Spawns `sys.executable -m touchstone …` (inherits the GUI's venv/interpreter)."""

    def __init__(self) -> None:
        self._procs: dict[int, subprocess.Popen[bytes]] = {}

    def spawn(self, argv: list[str]) -> int:
        proc = subprocess.Popen([sys.executable, "-m", "touchstone", *argv])
        self._procs[proc.pid] = proc
        return proc.pid

    def alive(self, pid: int) -> bool:
        proc = self._procs.get(pid)
        if proc is not None:
            return proc.poll() is None
        return _pid_alive(pid)

    def terminate(self, pid: int) -> None:
        proc = self._procs.get(pid)
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


class RunRegistry:
    """One active measurement run at a time, enforced by the on-disk sentinel lock."""

    def __init__(self, runs_dir: Path, launcher: ProcessLauncher) -> None:
        self.runs_dir = runs_dir
        self.launcher = launcher

    # ---- lock ----
    def _active_run_dir(self) -> Path | None:
        """Scan runs/ for an active (running + live pid) sentinel; reclaim stale ones."""
        if not self.runs_dir.exists():
            return None
        for child in self.runs_dir.iterdir():
            if not child.is_dir():
                continue
            s = read_sentinel(child)
            if s is None or s.get("state") != "running":
                continue
            if self.launcher.alive(int(s["pid"])):
                return child
            mark_sentinel(child, "failed")  # stale: reclaim the lock
        return None

    def _guard_free(self) -> None:
        active = self._active_run_dir()
        if active is not None:
            raise RunInProgress(f"a measurement run is active in {active}")

    # ---- start ----
    def start_eval(self, *, pack_path: str, config_path: str, resume_dir: Path | None = None) -> RunHandle:
        self._guard_free()
        run_dir = resume_dir or self._new_run_dir(pack_path)
        argv = ["eval", "--pack", pack_path, "--config", config_path,
                "--run-dir", str(run_dir), "--emit-events"]
        if resume_dir is not None:
            argv += ["--resume", str(resume_dir)]
        pid = self.launcher.spawn(argv)
        write_sentinel(run_dir, kind="eval", pid=pid, pack_path=pack_path, config_path=config_path)
        return RunHandle("eval", run_dir, pid)

    def start_judge(self, *, bundle: Path, judge_config_path: str) -> RunHandle:
        self._guard_free()
        argv = ["judge", "--bundle", str(bundle),
                "--judge-config", judge_config_path, "--emit-events"]
        pid = self.launcher.spawn(argv)
        write_sentinel(bundle, kind="judge", pid=pid,
                       pack_path="", config_path=judge_config_path)
        return RunHandle("judge", bundle, pid)

    def _new_run_dir(self, pack_path: str) -> Path:
        from touchstone.pack import load_pack
        pk = load_pack(pack_path)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        return self.runs_dir / f"{ts}_eval_{pk.id}"

    # ---- lifecycle ----
    def poll(self, handle: RunHandle) -> str:
        if self.launcher.alive(handle.pid):
            return "running"
        s = read_sentinel(handle.run_dir)
        state = s.get("state") if s else None
        if state in {"stopped", "failed"}:
            return str(state)
        mark_sentinel(handle.run_dir, "finished")
        return "finished"

    def stop(self, handle: RunHandle) -> None:
        self.launcher.terminate(handle.pid)
        mark_sentinel(handle.run_dir, "stopped")
```

> **Note for judge `--emit-events`:** Task 7-bis below adds `--emit-events` to the `judge` command (mirrors Task 2 for eval). If implementing strictly in order, add it now as a small extension of Task 2's pattern: `judge` gets an `emit_events` flag, `emit = web or emit_events`, and the writers run inside `contextlib.nullcontext` when not `web`. Keep `judge_events.jsonl` truncate mode (already the case).

- [ ] **Step 4: Add `judge --emit-events` (mirror of Task 2)**

In `judge()` add `emit_events: bool = typer.Option(False, "--emit-events", ...)` and restructure the tail of the function so `emit = web or emit_events`; when `emit` but not `web`, run the `_judge_event_writers` + `_judge_and_persist(..., on_verdict=on_verdict)` + `write_masters` + `judge_done` block inside `contextlib.nullcontext()` instead of `_live_monitor`, and skip `_hold_monitor`. Guard the new test:

```python
# add to tests/test_gui_cli_runargs.py
def test_judge_emit_events_no_monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "touchstone.cli._live_monitor",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no monitor")),
    )
    # minimal bundle
    b = tmp_path / "bundle"
    b.mkdir()
    (b / "bundle.json").write_text('{"pack_path":"packs/ndassist.yaml","host":{}}', encoding="utf-8")
    (b / "responses.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr("touchstone.cli.load_responses_jsonl", lambda p: [])
    monkeypatch.setattr("touchstone.cli._judge_and_persist", lambda *a, **k: ([], []))
    monkeypatch.setattr("touchstone.cli._render_judge_scorecard", lambda *a, **k: None)
    monkeypatch.setattr(
        "touchstone.cli.OpenAIJudgeBackend", lambda *a, **k: object()
    )
    monkeypatch.setattr("touchstone.cli.load_judge_config", lambda p: __import__("types").SimpleNamespace(
        endpoint=__import__("types").SimpleNamespace(base_url="x", api_key="y"), model="m", temperature=0.0))
    res = runner.invoke(app, ["judge", "--bundle", str(b), "--judge-config", "judge.yaml", "--emit-events"])
    assert res.exit_code == 0, res.output
    assert (b / "judge_events.jsonl").exists()
```

- [ ] **Step 5: Run tests + types**

Run: `uv run pytest tests/test_gui_control_registry.py tests/test_gui_cli_runargs.py -q && uv run mypy touchstone/`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add touchstone/gui/control.py touchstone/cli.py tests/test_gui_control_registry.py tests/test_gui_cli_runargs.py
git commit -m "feat(gui): ProcessLauncher + RunRegistry (one-run sentinel lock) + judge --emit-events

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `bundles.py` — discovery, classification & verdict recompute

**Why:** Station 1/6 need a classified list of `runs/`; Station 5 needs per-bundle detail with the recomputed verdict. `bundle.json` carries no status/verdict — both are derived.

**Files:**
- Create: `touchstone/gui/bundles.py`
- Test: `tests/test_gui_bundles.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_bundles.py
import json

from touchstone.gui import bundles


def _mk(d, *, bundle=False, scores=False, responses=False, sentinel_state=None):
    d.mkdir(parents=True, exist_ok=True)
    if bundle:
        (d / "bundle.json").write_text(json.dumps({
            "pack_id": "ndassist", "pack_path": "packs/ndassist.yaml",
            "models": [{"id": "qwen2.5:3b", "quant": "q"}], "date": "2026-06-20",
        }), encoding="utf-8")
    if responses:
        (d / "responses.jsonl").write_text("", encoding="utf-8")
    if scores:
        (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    if sentinel_state:
        (d / "run.json").write_text(json.dumps(
            {"kind": "eval", "pid": 1, "state": sentinel_state, "run_dir": str(d)}), encoding="utf-8")


def test_classify_judged(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _mk(d, bundle=True, responses=True, scores=True)
    s = bundles.classify(d)
    assert s.status == "judged"


def test_classify_eval_only(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _mk(d, bundle=True, responses=True)
    assert bundles.classify(d).status == "eval-only"


def test_classify_crashed(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _mk(d, responses=True, sentinel_state="failed")
    assert bundles.classify(d).status == "crashed"


def test_legacy_run_dir_ignored(tmp_path):
    d = tmp_path / "2026_plainrun"
    d.mkdir()
    (d / "raw.csv").write_text("x\n", encoding="utf-8")
    assert bundles.classify(d) is None


def test_discover_lists_only_bundles(tmp_path):
    _mk(tmp_path / "a_eval_nd", bundle=True, responses=True, scores=True)
    (tmp_path / "legacy").mkdir()
    (tmp_path / "legacy" / "raw.csv").write_text("x\n", encoding="utf-8")
    found = bundles.discover(tmp_path)
    assert [b.run_dir.name for b in found] == ["a_eval_nd"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_bundles.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `bundles.py`**

```python
# touchstone/gui/bundles.py
"""Read-only discovery of runs/: classify each dir and (for judged bundles) recompute the
verdict via the shared scorecard math. bundle.json carries neither status nor verdict."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from touchstone.gui import control


@dataclass
class BundleSummary:
    run_dir: Path
    status: str                       # running | crashed | eval-only | judged
    pack_id: str = ""
    models: list[str] = field(default_factory=list)
    date: str = ""
    recommendation: str | None = None  # only when judged
    safety_passed: bool | None = None


def classify(run_dir: Path) -> BundleSummary | None:
    """Classify one dir; return None for legacy run/embed dirs (no bundle.json + no sentinel)."""
    sentinel = control.read_sentinel(run_dir)
    has_bundle = (run_dir / "bundle.json").exists()
    has_scores = (run_dir / "scores.csv").exists()
    has_responses = (run_dir / "responses.jsonl").exists()

    if sentinel is not None and control.is_active(sentinel):
        return _summary(run_dir, "running", sentinel)
    if sentinel is not None and not has_bundle:
        # sentinel present, pid dead, never finalized → crashed/resumable
        return _summary(run_dir, "crashed", sentinel)
    if not has_bundle:
        return None  # legacy run/embed dir
    if has_scores:
        return _judged_summary(run_dir)
    if has_responses:
        return _summary(run_dir, "eval-only", sentinel)
    return _summary(run_dir, "eval-only", sentinel)


def _manifest(run_dir: Path) -> dict[str, Any]:
    p = run_dir / "bundle.json"
    if not p.exists():
        return {}
    try:
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _summary(run_dir: Path, status: str, sentinel: dict[str, Any] | None) -> BundleSummary:
    m = _manifest(run_dir)
    return BundleSummary(
        run_dir=run_dir,
        status=status,
        pack_id=str(m.get("pack_id", sentinel.get("kind", "") if sentinel else "")),
        models=[mm["id"] for mm in m.get("models", [])],
        date=str(m.get("date", "")),
    )


def _judged_summary(run_dir: Path) -> BundleSummary:
    base = _summary(run_dir, "judged", None)
    rec, passed = _recompute_verdict(run_dir)
    base.recommendation = rec
    base.safety_passed = passed
    return base


def _recompute_verdict(run_dir: Path) -> tuple[str | None, bool | None]:
    """Recompute the (best) verdict via the shared scorecard.master_rows."""
    from touchstone import scorecard
    from touchstone.judge import load_judgements_jsonl
    from touchstone.pack import load_pack
    from touchstone.qualrun import load_responses_jsonl

    m = _manifest(run_dir)
    pack_path = m.get("pack_path")
    if not pack_path or not Path(pack_path).exists():
        return None, None
    pk = load_pack(pack_path)
    responses = load_responses_jsonl(run_dir / "responses.jsonl")
    verdicts = load_judgements_jsonl(run_dir / "judgements.jsonl")
    # master dims need a holistic ModelReport; v1 reads it back via re-judge-free path:
    # if reports aren't persisted, derive an empty-report fallback → recommendation None.
    rows = scorecard.master_rows(pk, responses, verdicts, _reports_from_scores(run_dir, pk))
    if not rows:
        return None, None
    # pick the strongest recommendation for the badge
    order = {"Ja": 3, "Mit Einschränkung": 2, "Nein": 1}
    best = max(rows, key=lambda r: order.get(str(r["recommendation"]), 0))
    return str(best["recommendation"]), bool(best["safety_passed"])


def _reports_from_scores(run_dir: Path, pk: Any) -> list[Any]:
    """Reconstruct ModelReport.dim_scores from scores.csv (metric_type='dimension' rows)."""
    import csv

    from touchstone.results import ModelReport

    p = run_dir / "scores.csv"
    if not p.exists():
        return []
    by: dict[tuple[str, str], dict[str, int]] = {}
    with p.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("metric_type") != "dimension":
                continue
            key = (row["model"], row["variant"])
            by.setdefault(key, {})[row["metric"]] = int(float(row["score"]))
    return [ModelReport(m, v, dims, {}) for (m, v), dims in by.items()]


def discover(runs_dir: Path) -> list[BundleSummary]:
    if not runs_dir.exists():
        return []
    out: list[BundleSummary] = []
    for child in sorted(runs_dir.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        s = classify(child)
        if s is not None:
            out.append(s)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_bundles.py -v`
Expected: PASS.

- [ ] **Step 5: Add a judged-verdict integration test against the real bundle**

```python
# add to tests/test_gui_bundles.py
import os
import pytest

REAL = "runs/2026-06-20_104844_eval_ndassist"


@pytest.mark.skipif(not os.path.isdir(REAL), reason="real bundle not present")
def test_recompute_verdict_real_bundle():
    from pathlib import Path
    s = bundles.classify(Path(REAL))
    assert s is not None and s.status == "judged"
    assert s.recommendation in {"Ja", "Mit Einschränkung", "Nein"}
```

Run: `uv run pytest tests/test_gui_bundles.py -q`
Expected: PASS (or skip if the bundle isn't present).

- [ ] **Step 6: Types + commit**

```bash
uv run mypy touchstone/gui/
git add touchstone/gui/bundles.py tests/test_gui_bundles.py
git commit -m "feat(gui): bundle discovery/classification + verdict recompute (scorecard.master_rows)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: `live.py` — tail + build_view → live view dicts

**Why:** The SSE source. Reuses `tail.read_new` + `events/judge_events.build_view` verbatim (no aggregation duplicated). Per spawn, eval tails truncate-fresh `events.jsonl` (offset 0); judge tails `judge_events.jsonl`.

**Files:**
- Create: `touchstone/gui/live.py`
- Test: `tests/test_gui_live.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_live.py
from touchstone import events as ev
from touchstone.gui import live


def test_eval_stream_folds_events(tmp_path):
    p = tmp_path / "events.jsonl"
    lines = [
        ev.dumps(ev.run_start_event(1.0, 2)),
        ev.dumps(ev.cell_start_event(1.1, 0, "m", "v", "A", "A1", 0)),
        ev.dumps(ev.cell_done_event(1.2, 0, "m", "v", "A1", 0, True, 0.3, 1.0, 5.0, 7, False, "")),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stream = live.LiveStream(p, kind="eval")
    view = stream.snapshot()
    assert view["total"] == 2 and view["done"] == 1
    assert view["finished"] is False


def test_stream_tolerates_missing_file(tmp_path):
    stream = live.LiveStream(tmp_path / "nope.jsonl", kind="eval")
    view = stream.snapshot()
    assert view["total"] == 0 and view["done"] == 0


def test_judge_stream_uses_judge_view(tmp_path):
    from touchstone import judge_events as je
    p = tmp_path / "judge_events.jsonl"
    p.write_text(je.dumps(je.judge_start_event(1.0, 3)) + "\n", encoding="utf-8")
    stream = live.LiveStream(p, kind="judge")
    assert stream.snapshot()["total"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_live.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `live.py`**

```python
# touchstone/gui/live.py
"""Live SSE source: tail an event file and fold it with the existing pure build_view.
The aggregation math (histogram, ETA, dedup) is reused verbatim; only the transport is new."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from touchstone import events as events_mod
from touchstone import judge_events as judge_events_mod
from touchstone import tail

_VIEWS = {"eval": events_mod, "judge": judge_events_mod}


class LiveStream:
    """Stateful tailer: holds the byte offset + accumulated events, re-folds on each read."""

    def __init__(self, events_path: Path, *, kind: str) -> None:
        if kind not in _VIEWS:
            raise ValueError(f"unknown kind {kind!r}")
        self.events_path = events_path
        self.view_mod = _VIEWS[kind]
        self._offset = 0
        self._events: list[dict[str, Any]] = []

    def _ingest(self) -> None:
        lines, self._offset = tail.read_new(self.events_path, self._offset)
        for ln in lines:
            parsed = self.view_mod.parse_line(ln)
            if parsed is not None:
                self._events.append(parsed)

    def snapshot(self) -> dict[str, Any]:
        """Read any new lines, fold the whole stream, return the render-ready view dict."""
        self._ingest()
        view = self.view_mod.build_view(self._events)
        result: dict[str, Any] = view.as_dict()
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_live.py -v`
Expected: PASS.

- [ ] **Step 5: Types + commit**

```bash
uv run mypy touchstone/gui/
git add touchstone/gui/live.py tests/test_gui_live.py
git commit -m "feat(gui): LiveStream — tail + build_view reuse (SSE source)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `app.py` — FastAPI factory + read routes (overview, pack, result, compare, export)

**Why:** The read half of the 7 stations. `create_app(runs_dir, registry)` returns a FastAPI app; routes render Jinja fragments from the reuse layer. Tested with `TestClient`.

**Files:**
- Create: `touchstone/gui/app.py` (factory + read routes; templates referenced are added in Task 11 but minimal inline strings keep tests green here)
- Test: `tests/test_gui_app_read.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_app_read.py
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from touchstone.gui import app as gui_app  # noqa: E402
from touchstone.gui.control import RunRegistry  # noqa: E402


class _FakeLauncher:
    def spawn(self, argv): return 1
    def alive(self, pid): return False
    def terminate(self, pid): return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _mk_judged(tmp_path):
    d = tmp_path / "2026-06-20_eval_ndassist"
    d.mkdir(parents=True)
    (d / "bundle.json").write_text(json.dumps({
        "pack_id": "ndassist", "pack_path": "packs/ndassist.yaml",
        "models": [{"id": "qwen2.5:3b", "quant": "q"}], "date": "2026-06-20"}), encoding="utf-8")
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    return d


def test_overview_lists_bundles(tmp_path):
    _mk_judged(tmp_path)
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "ndassist" in r.text


def test_pack_explorer_renders_dimensions(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    # route reads the pack file by path param; ndassist has dimensions Q1..Q7
    assert r.status_code == 200
    assert "Q1" in r.text or "Sicherheit" in r.text


def test_compare_renders_table(tmp_path):
    _mk_judged(tmp_path)
    r = _client(tmp_path).get("/compare")
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_app_read.py -v`
Expected: FAIL — `create_app` missing.

- [ ] **Step 3: Implement the factory + read routes**

```python
# touchstone/gui/app.py
"""FastAPI control-center. create_app() wires the 7 station routes over the reuse layer.
Templates/static are mounted from this package; routes return HTMX-friendly HTML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from touchstone import aggregate as aggregate_mod
from touchstone.gui import bundles
from touchstone.gui.control import RunRegistry
from touchstone.pack import load_pack

_PKG = Path(__file__).parent
_templates = Jinja2Templates(directory=str(_PKG / "templates"))


def create_app(*, runs_dir: Path, registry: RunRegistry) -> FastAPI:
    app = FastAPI(title="touchstone", docs_url=None, redoc_url=None)
    static_dir = _PKG / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def render(name: str, request: Request, **ctx: Any) -> HTMLResponse:
        return _templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> HTMLResponse:
        items = bundles.discover(runs_dir)
        return render("overview.html", request, bundles=items)

    @app.get("/packs/{pack_path:path}", response_class=HTMLResponse)
    def pack_explorer(request: Request, pack_path: str) -> HTMLResponse:
        pk = load_pack(pack_path)
        return render("pack.html", request, pack=pk)

    @app.get("/result/{name}", response_class=HTMLResponse)
    def result(request: Request, name: str) -> HTMLResponse:
        rd = runs_dir / name
        summary = bundles.classify(rd)
        return render("result.html", request, summary=summary, run_dir=rd)

    @app.get("/compare", response_class=HTMLResponse)
    def compare(request: Request) -> HTMLResponse:
        rows = aggregate_mod.load_all_scores(runs_dir)
        agg = aggregate_mod.aggregate(rows) if rows else []
        return render("compare.html", request, rows=agg)

    @app.get("/export/{name}/{fname}")
    def export(name: str, fname: str) -> Any:
        from fastapi.responses import FileResponse
        # only ledger files are exportable (transient event/sentinel files excluded — G10)
        allowed = {"scorecard.md", "scores.csv", "perf.csv", "report.md", "aggregate.md"}
        if fname not in allowed:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return FileResponse(runs_dir / name / fname)

    _register_control_routes(app, runs_dir=runs_dir, registry=registry)  # Task 10
    return app
```

> Until Task 10 is implemented, define a temporary no-op:
> ```python
> def _register_control_routes(app, *, runs_dir, registry): ...
> ```
> at the bottom of `app.py`; Task 10 replaces it.

- [ ] **Step 4: Add the minimal templates needed for these tests to pass**

Create `touchstone/gui/templates/base.html`, `overview.html`, `pack.html`, `result.html`, `compare.html` as minimal valid pages (Task 11 styles them). Minimum to pass the assertions:

```html
<!-- touchstone/gui/templates/base.html -->
<!doctype html><html><head><meta charset="utf-8"><title>touchstone</title>
<script src="/static/htmx.min.js"></script><link rel="stylesheet" href="/static/app.css"></head>
<body><nav><a href="/">Übersicht</a> · <a href="/compare">Vergleich</a></nav>
<main>{% block body %}{% endblock %}</main></body></html>
```

```html
<!-- overview.html -->{% extends "base.html" %}{% block body %}
<h1>Übersicht</h1><table>{% for b in bundles %}
<tr><td><a href="/result/{{ b.run_dir.name }}">{{ b.run_dir.name }}</a></td>
<td>{{ b.pack_id }}</td><td>{{ b.status }}</td>
<td>{% if b.recommendation %}{{ b.recommendation }}{% endif %}</td></tr>{% endfor %}</table>
{% endblock %}
```

```html
<!-- pack.html -->{% extends "base.html" %}{% block body %}
<h1>{{ pack.title }}</h1><ul>{% for d in pack.dimensions %}
<li>{{ d.id }} · {{ d.name }} · ×{{ d.weight }}</li>{% endfor %}</ul>
<p>K.-o.: {{ pack.ko_rule.dimension }} ≤ {{ pack.ko_rule.threshold }}</p>{% endblock %}
```

```html
<!-- result.html -->{% extends "base.html" %}{% block body %}
<h1>{{ run_dir.name }}</h1>{% if summary %}<p>Status: {{ summary.status }}</p>
{% if summary.recommendation %}<p>Urteil: {{ summary.recommendation }}</p>{% endif %}{% endif %}
{% endblock %}
```

```html
<!-- compare.html -->{% extends "base.html" %}{% block body %}
<h1>Vergleich</h1><table>{% for r in rows %}<tr><td>{{ r.model }}</td>
<td>{{ r.quality_pct }}</td></tr>{% endfor %}</table>{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_app_read.py -v`
Expected: PASS.

- [ ] **Step 6: Types + commit**

```bash
uv run mypy touchstone/gui/
git add touchstone/gui/app.py touchstone/gui/templates/ tests/test_gui_app_read.py
git commit -m "feat(gui): FastAPI factory + read routes (overview/pack/result/compare/export)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `app.py` — control routes (start/stop/resume) + live SSE

**Why:** Station 3 (steering) + Station 4 (live). POST endpoints drive the registry; GET `/live/{name}` streams the folded view.

**Files:**
- Modify: `touchstone/gui/app.py` (replace `_register_control_routes`)
- Test: `tests/test_gui_app_control.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_app_control.py
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from touchstone.gui import app as gui_app  # noqa: E402
from touchstone.gui.control import RunInProgress, RunRegistry  # noqa: E402


class _FakeReg(RunRegistry):
    def __init__(self):
        self.started = []
        self.stopped = []
    def start_eval(self, *, pack_path, config_path, resume_dir=None):
        from touchstone.gui.control import RunHandle
        from pathlib import Path
        self.started.append(("eval", pack_path, config_path))
        return RunHandle("eval", Path("runs/x"), 1)
    def stop(self, handle):
        self.stopped.append(handle)


def _client(tmp_path, reg):
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def test_start_eval_calls_registry(tmp_path):
    reg = _FakeReg()
    r = _client(tmp_path, reg).post(
        "/runs/eval", data={"pack_path": "packs/ndassist.yaml", "config_path": "config.m5.yaml"}
    )
    assert r.status_code in (200, 303)
    assert reg.started and reg.started[0][1] == "packs/ndassist.yaml"


def test_start_blocked_returns_conflict(tmp_path):
    class _Busy(_FakeReg):
        def start_eval(self, **k):
            raise RunInProgress("busy")
    r = _client(tmp_path, _Busy()).post(
        "/runs/eval", data={"pack_path": "p", "config_path": "c"}
    )
    assert r.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gui_app_control.py -v`
Expected: FAIL — control routes are a no-op.

- [ ] **Step 3: Implement `_register_control_routes`**

Replace the no-op at the bottom of `app.py`:

```python
def _register_control_routes(app: FastAPI, *, runs_dir: Path, registry: RunRegistry) -> None:
    import json as _json

    from fastapi import Form, HTTPException
    from fastapi.responses import StreamingResponse

    from touchstone.gui import live as live_mod
    from touchstone.gui.control import RunInProgress

    @app.post("/runs/eval")
    def start_eval(pack_path: str = Form(...), config_path: str = Form(...)) -> Any:
        try:
            h = registry.start_eval(pack_path=pack_path, config_path=config_path)
        except RunInProgress as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"run_dir": h.run_dir.name, "kind": h.kind}

    @app.post("/runs/judge")
    def start_judge(bundle: str = Form(...), judge_config_path: str = Form(...)) -> Any:
        try:
            h = registry.start_judge(bundle=runs_dir / bundle, judge_config_path=judge_config_path)
        except RunInProgress as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"run_dir": h.run_dir.name, "kind": h.kind}

    @app.post("/runs/stop")
    def stop_run(name: str = Form(...)) -> Any:
        from touchstone.gui.control import RunHandle, read_sentinel
        s = read_sentinel(runs_dir / name)
        if s is None:
            raise HTTPException(status_code=404)
        registry.stop(RunHandle(str(s["kind"]), runs_dir / name, int(s["pid"])))
        return {"stopped": name}

    @app.get("/live/{name}")
    def live_stream(name: str, kind: str = "eval") -> StreamingResponse:
        fname = "events.jsonl" if kind == "eval" else "judge_events.jsonl"
        stream = live_mod.LiveStream(runs_dir / name / fname, kind=kind)

        def gen() -> Any:
            import time as _t
            for _ in range(100000):
                view = stream.snapshot()
                yield f"data: {_json.dumps(view)}\n\n"
                if view.get("finished"):
                    break
                _t.sleep(0.25)

        return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gui_app_control.py -v`
Expected: PASS.

- [ ] **Step 5: Full GUI suite + types + commit**

```bash
uv run pytest tests/test_gui_*.py -q && uv run mypy touchstone/gui/
git add touchstone/gui/app.py tests/test_gui_app_control.py
git commit -m "feat(gui): control routes (start/stop/resume) + live SSE stream

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Front-end (app-shell, station templates, vendored assets) + `serve()`

**Why:** The net-new presentation half (G11) and the `serve()` entry point Task 4's command imports. Not TDD — route tests already assert content; this is the styled UI from the approved mockup.

**Files:**
- Create: `touchstone/gui/static/htmx.min.js`, `alpine.min.js`, `app.css`
- Modify: `touchstone/gui/templates/*.html` (style to the mockup: sidebar nav, run card, badge table)
- Modify: `touchstone/gui/app.py` (add `serve()`)

- [ ] **Step 1: Vendor the JS assets**

```bash
mkdir -p touchstone/gui/static
curl -sL https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js -o touchstone/gui/static/htmx.min.js
curl -sL https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js -o touchstone/gui/static/alpine.min.js
test -s touchstone/gui/static/htmx.min.js && test -s touchstone/gui/static/alpine.min.js && echo OK
```

(If offline, the plan's executor should note it and fall back to a `<script>` CDN tag in `base.html` with a TODO to vendor later — but prefer vendored.)

- [ ] **Step 2: Write `app.css`** — sidebar layout, cards, badges matching the mockup (success/warning/error verdict colors). Keep it hand-written (no Tailwind build); ~150 lines. Base it on the mockup's structure: `.sidebar`, `.nav-item.active`, `.run-card`, `.badge.ja/.einschr/.nein`, `.bundle-table`.

- [ ] **Step 3: Flesh out the templates** to the approved mockup: `base.html` gets the sidebar (7 nav links + hardware/endpoint status); `overview.html` gets the "Neuer Lauf" button, the running-run card (with stop button + live progress via `hx-ext="sse"` against `/live/{name}`), and the bundle table with verdict badges; `pack.html` shows the full pack tree (dimensions/weights/ko_rule/variants/prompts+flags); `result.html` renders the Jinja tables (tech-specs + master via `scorecard.master_rows`); `compare.html` the Hardware×Quality table; add a `config.html` (Station 3) form posting to `/runs/eval` + `/runs/judge` with pack/config/judge-config dropdowns (list `packs/*.yaml`, `config*.yaml`, `judge*.yaml`).

- [ ] **Step 4: Add `serve()` to `app.py`**

```python
def serve(*, runs_dir: Path, port: int = 0, open_browser: bool = True) -> None:
    """Entry point for `touchstone gui`: build the app, bind, optionally open the browser."""
    import socket
    import threading
    import webbrowser

    import uvicorn

    from touchstone.gui.control import RealProcessLauncher

    registry = RunRegistry(runs_dir=runs_dir, launcher=RealProcessLauncher())
    app = create_app(runs_dir=runs_dir, registry=registry)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    bound_port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{bound_port}"
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    config = uvicorn.Config(app, log_level="warning")
    server = uvicorn.Server(config)
    server.run(sockets=[sock])
```

- [ ] **Step 5: Manual visual check**

Run: `uv run touchstone gui --no-open` then open the printed URL; confirm the overview renders the real `runs/` bundles with badges, the pack explorer shows the ndassist tree, and the compare table loads. (Functional smoke is Task 12.)

- [ ] **Step 6: Commit**

```bash
git add touchstone/gui/static/ touchstone/gui/templates/ touchstone/gui/app.py
git commit -m "feat(gui): styled front-end (sidebar shell, stations, vendored htmx/alpine) + serve()

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: End-to-end smoke + AGENTS.md + final gate

**Files:**
- Modify: `AGENTS.md`
- No new code; this is the integration gate.

- [ ] **Step 1: Full test suite + lint + types**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy touchstone/`
Expected: all green. Fix any fallout before proceeding.

- [ ] **Step 2: Live smoke (manual, requires the M5 endpoint :1234 + judge :1234)**

1. `uv run touchstone gui` → browser opens.
2. Station "Konfig + Start": pick `packs/ndassist.yaml` + `config.m5.yaml` → **eval starten**. Overview shows the running card; progress climbs via SSE.
3. **Stoppen** → process ends cleanly (no zombie: `pgrep -f "touchstone eval"` empty). Bundle shows as crashed/resumable.
4. **Fortsetzen** → live view does NOT show false "fertig"; run completes.
5. Pick a judge-config → **judge starten** → scorecard appears in the result view.
6. Compare shows the aggregate; Export downloads `scorecard.md`.
7. Restart `touchstone gui` mid-run → overview shows "running"; a second start is refused (409).

- [ ] **Step 3: Update AGENTS.md**

Add `touchstone gui` to the Commands block and an architecture note under Gotchas:

```markdown
uv run touchstone gui                            # local web control-center (needs the [gui] extra)
```

> **The GUI is an optional `[gui]` extra and spawns measurement subprocesses.** `touchstone gui`
> (FastAPI + vendored HTMX/Alpine) never measures in-process: it spawns `touchstone eval/judge`
> with `--run-dir <host-chosen>` `--emit-events` (writers without the webmon monitor) and tails
> their `events.jsonl`. `runs/` stays SSOT; the GUI's only state is a transient **run-sentinel**
> (`run.json`) that triples as run_dir handle, cross-process **one-run lock** (survives a GUI
> restart), and discovery anchor for running/crashed runs. Event/sentinel files are transient,
> excluded from discovery + export. Only one measurement run at a time (mess-cleanliness).
```

- [ ] **Step 4: Commit + branch-finish**

```bash
git add AGENTS.md
git commit -m "docs(agents): touchstone gui command + control-plane architecture notes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

Then use `superpowers:finishing-a-development-branch` to decide merge/PR (do NOT push without the user's go-ahead — push is a hard blocker per project memory).

---

## Self-Review Notes (author checklist — completed)

- **Spec coverage:** G1–G11 each map to a task — G2/G9→T4, G3/G4/G8→T5/T6, G5→task ordering, G6→T6 step 4, G7→T8, G10→T2 (truncate) + T9 (export filter), G11→T11. Stations 1–7 → T7/T9/T10/T11.
- **Type consistency:** `RunHandle`, `RunRegistry`, `BundleSummary`, `LiveStream`, `create_app(runs_dir, registry)`, `scorecard.master_rows` names are used identically across tasks.
- **Known sequencing note:** Task 9's routes reference templates created in Task 9 step 4 (minimal) then styled in Task 11 — tests pass at Task 9; visuals land at Task 11. `serve()` (imported by Task 4's command) is defined in Task 11; the Task 4 test only exercises the missing-extra branch, so order holds.
- **Open detail deferred to executor:** exact `judge()` restructuring mirrors Task 2's eval pattern (T6 step 4); Tailwind decision settled as hand-written `app.css` (no build).
