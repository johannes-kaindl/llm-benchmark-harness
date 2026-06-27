# Nacht-Queue (`touchstone queue`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein CLI-Command `touchstone queue queue.yaml`, der mehrere Modelle über Nacht sequenziell als isolierte Subprozesse durchläuft (pro Eintrag `eval → judge`), mit Reset+Settle zwischen Einträgen, continue-on-error, per-Step-Watchdog, inkrementellem Summary und Resume.

**Architecture:** Dünner sequenzieller Subprozess-Orchestrator. Pure Logik (Schema, Settle-Wait, Slug, Summary, Resume, Step-Klassifikation, Argv-Bau) in `touchstone/runqueue.py` mit Dependency-Injection von Subprozess-Spawn/RAM-Poll/Clock/Sleep; die Verdrahtung mit echtem `subprocess`/`psutil`/`time` lebt im dünnen `queue`-Command in `cli.py`.

**Tech Stack:** Python 3.12, uv, pydantic, Typer, psutil, pytest. Ruff (line-length 100) + mypy strict.

## Global Constraints

- Python 3.12, `uv` only. Ruff line-length 100, mypy strict, alle Tests grün (`uv run pytest -q`).
- Code/Identifier englisch; Doku/Prosa/Commit-Beschreibungen dürfen deutsch sein.
- **Kein engine-spezifischer Code im Hot-Path.** Der Reset (`lms unload …`) ist ein *konfigurierbares* Shell-Kommando (Daten), kein Engine-Branch.
- I/O dependency-injected, damit ohne Server/sudo testbar (`stream_once(clock=…)`-Ethos).
- Neues Modul heißt `runqueue.py` (**nicht** `queue.py` → würde stdlib `queue` shadowen).
- Ein Modell pro Eintrag (frische RAM-Baseline, [[multimodel-ram-confound]]).

---

### Task 1: Schema + `load_queue` + `run_dir_for`

**Files:**
- Create: `touchstone/runqueue.py`
- Test: `tests/test_runqueue.py`

**Interfaces:**
- Consumes: `touchstone.config.ModelSpec`
- Produces:
  - `SettleSpec(timeout_s: float=120, plateau_polls: int=3, poll_interval_s: float=2, epsilon_mb: float=200)`
  - `StepTimeouts(eval_s: float=14400, judge_s: float=21600)` (Feldname `eval_s`/`judge_s`; YAML-Keys `eval`/`judge` via alias)
  - `QueueDefaults(reset_command: str="lms unload --all", settle: SettleSpec, step_timeout_s: StepTimeouts, cooldown_s: float=0)`
  - `QueueEntry(config: Path, pack: Path, model: ModelSpec, judge_config: Path|None=None, judge_model: str="", reset_command: str|None=None, settle: SettleSpec|None=None, step_timeout_s: StepTimeouts|None=None, cooldown_s: float|None=None)`
  - `QueueSpec(defaults: QueueDefaults, entries: list[QueueEntry])`
  - `load_queue(path: str|Path) -> QueueSpec` — raises `ValueError` on bad YAML / missing referenced files / invalid model
  - `run_dir_for(ts: str, model_id: str, pack_id: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runqueue.py
from pathlib import Path
import pytest
from touchstone import runqueue as rq

def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8"); return p

def test_load_queue_minimal(tmp_path: Path):
    cfg = _write(tmp_path / "c.yaml", "endpoint:\n  base_url: x\nmodels:\n  - id: m\n")
    pack = _write(tmp_path / "p.yaml", "id: pk\n")
    q = _write(tmp_path / "q.yaml", f"""
entries:
  - config: {cfg}
    pack: {pack}
    model: {{ id: "a/b" }}
""")
    spec = rq.load_queue(q)
    assert len(spec.entries) == 1
    assert spec.entries[0].model.id == "a/b"
    assert spec.entries[0].judge_config is None
    # defaults applied
    assert spec.defaults.reset_command == "lms unload --all"
    assert spec.defaults.step_timeout_s.judge_s == 21600

def test_load_queue_string_model_shorthand(tmp_path: Path):
    cfg = _write(tmp_path / "c.yaml", "endpoint:\n  base_url: x\nmodels:\n  - id: m\n")
    pack = _write(tmp_path / "p.yaml", "id: pk\n")
    q = _write(tmp_path / "q.yaml", f"entries:\n  - config: {cfg}\n    pack: {pack}\n    model: \"only/id\"\n")
    spec = rq.load_queue(q)
    assert spec.entries[0].model.id == "only/id"

def test_load_queue_missing_config_raises(tmp_path: Path):
    pack = _write(tmp_path / "p.yaml", "id: pk\n")
    q = _write(tmp_path / "q.yaml", f"entries:\n  - config: {tmp_path/'nope.yaml'}\n    pack: {pack}\n    model: m\n")
    with pytest.raises(ValueError, match="config"):
        rq.load_queue(q)

def test_load_queue_defaults_override_per_entry(tmp_path: Path):
    cfg = _write(tmp_path / "c.yaml", "endpoint:\n  base_url: x\nmodels:\n  - id: m\n")
    pack = _write(tmp_path / "p.yaml", "id: pk\n")
    q = _write(tmp_path / "q.yaml", f"""
defaults:
  cooldown_s: 5
  step_timeout_s: {{ eval: 10, judge: 20 }}
entries:
  - config: {cfg}
    pack: {pack}
    model: m
    cooldown_s: 99
""")
    spec = rq.load_queue(q)
    assert spec.defaults.cooldown_s == 5
    assert spec.entries[0].cooldown_s == 99
    assert spec.defaults.step_timeout_s.eval_s == 10

def test_run_dir_for_sanitizes_slashes():
    assert rq.run_dir_for("2026-06-27_2200", "qwen/qwen3.6-27b", "buero") == "2026-06-27_2200_qwen-qwen3.6-27b_eval_buero"
    # deterministic
    assert rq.run_dir_for("t", "a/b", "p") == rq.run_dir_for("t", "a/b", "p")
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_runqueue.py -q`
Expected: FAIL (`ModuleNotFoundError: touchstone.runqueue`).

- [ ] **Step 3: Implement schema + loaders**

```python
# touchstone/runqueue.py
from __future__ import annotations
import re
from pathlib import Path
import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator
from .config import ModelSpec

class SettleSpec(BaseModel):
    timeout_s: float = 120
    plateau_polls: int = 3
    poll_interval_s: float = 2
    epsilon_mb: float = 200

class StepTimeouts(BaseModel):
    eval_s: float = Field(default=14400, alias="eval")
    judge_s: float = Field(default=21600, alias="judge")
    model_config = {"populate_by_name": True}

class QueueDefaults(BaseModel):
    reset_command: str = "lms unload --all"
    settle: SettleSpec = Field(default_factory=SettleSpec)
    step_timeout_s: StepTimeouts = Field(default_factory=StepTimeouts)
    cooldown_s: float = 0

class QueueEntry(BaseModel):
    config: Path
    pack: Path
    model: ModelSpec
    judge_config: Path | None = None
    judge_model: str = ""
    reset_command: str | None = None
    settle: SettleSpec | None = None
    step_timeout_s: StepTimeouts | None = None
    cooldown_s: float | None = None

    @field_validator("model", mode="before")
    @classmethod
    def _coerce_model(cls, v: object) -> object:
        return {"id": v} if isinstance(v, str) else v

class QueueSpec(BaseModel):
    defaults: QueueDefaults = Field(default_factory=QueueDefaults)
    entries: list[QueueEntry]

def load_queue(path: str | Path) -> QueueSpec:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        spec = QueueSpec.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"invalid queue.yaml: {e}") from e
    if not spec.entries:
        raise ValueError("queue.yaml has no entries")
    for i, e in enumerate(spec.entries):
        if not e.config.exists():
            raise ValueError(f"entry {i}: config not found: {e.config}")
        if not e.pack.exists():
            raise ValueError(f"entry {i}: pack not found: {e.pack}")
        if e.judge_config is not None and not e.judge_config.exists():
            raise ValueError(f"entry {i}: judge_config not found: {e.judge_config}")
    return spec

def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")

def run_dir_for(ts: str, model_id: str, pack_id: str) -> str:
    return f"{ts}_{_slug(model_id)}_eval_{_slug(pack_id)}"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_runqueue.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff format touchstone/runqueue.py tests/test_runqueue.py && uv run ruff check touchstone/runqueue.py tests/test_runqueue.py && uv run mypy touchstone/runqueue.py
git add touchstone/runqueue.py tests/test_runqueue.py
git commit -m "feat(queue): queue.yaml schema + load_queue + run_dir_for slug"
```

---

### Task 2: `wait_until_settled` (Settle-Wait, pure + DI)

**Files:**
- Modify: `touchstone/runqueue.py`
- Test: `tests/test_runqueue.py`

**Interfaces:**
- Produces:
  - `SettleOutcome(settled: bool, waited_s: float, final_mb: float, polls: int)` (dataclass)
  - `wait_until_settled(ram_poll: Callable[[], float], sleep: Callable[[float], None], clock: Callable[[], float], *, settle: SettleSpec) -> SettleOutcome`
- Contract: pollt RAM bis Plateau (`|Δ| < epsilon_mb` für `plateau_polls` aufeinanderfolgende Polls) → `settled=True`; bei `clock()-start >= timeout_s` → `settled=False`. `sleep` muss in Tests den `clock` vorrücken.

- [ ] **Step 1: Write the failing tests**

```python
def _fake_time(seq_dt=2.0):
    t = {"now": 0.0}
    def clock(): return t["now"]
    def sleep(s): t["now"] += s
    return clock, sleep, t

def test_settle_reaches_plateau():
    vals = iter([5000, 4000, 3000, 2980, 2975, 2972])  # drops then flattens
    clock, sleep, _ = _fake_time()
    out = rq.wait_until_settled(lambda: next(vals), sleep, clock,
                                settle=rq.SettleSpec(timeout_s=100, plateau_polls=2,
                                                     poll_interval_s=2, epsilon_mb=50))
    assert out.settled is True
    assert out.final_mb == 2972

def test_settle_times_out_when_never_stable():
    n = {"v": 9000}
    def ram():
        n["v"] -= 1000  # always drops > epsilon
        return n["v"]
    clock, sleep, _ = _fake_time()
    out = rq.wait_until_settled(ram, sleep, clock,
                                settle=rq.SettleSpec(timeout_s=6, plateau_polls=3,
                                                     poll_interval_s=2, epsilon_mb=50))
    assert out.settled is False
    assert out.waited_s >= 6
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_runqueue.py -k settle -q` → FAIL (`AttributeError: wait_until_settled`).

- [ ] **Step 3: Implement**

```python
# add imports at top: from collections.abc import Callable; from dataclasses import dataclass
@dataclass
class SettleOutcome:
    settled: bool
    waited_s: float
    final_mb: float
    polls: int

def wait_until_settled(
    ram_poll: Callable[[], float],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    *,
    settle: SettleSpec,
) -> SettleOutcome:
    start = clock()
    prev = ram_poll()
    polls = 1
    stable = 0
    while True:
        if clock() - start >= settle.timeout_s:
            return SettleOutcome(False, clock() - start, prev, polls)
        sleep(settle.poll_interval_s)
        cur = ram_poll()
        polls += 1
        if abs(cur - prev) < settle.epsilon_mb:
            stable += 1
            if stable >= settle.plateau_polls:
                return SettleOutcome(True, clock() - start, cur, polls)
        else:
            stable = 0
        prev = cur
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_runqueue.py -k settle -q` → PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff format touchstone/runqueue.py tests/test_runqueue.py && uv run ruff check touchstone/runqueue.py && uv run mypy touchstone/runqueue.py
git add -u && git commit -m "feat(queue): condition-based settle-wait (RAM plateau / timeout, DI clock)"
```

---

### Task 3: Argv-Builder + Step-Klassifikation (pure)

**Files:**
- Modify: `touchstone/runqueue.py`
- Test: `tests/test_runqueue.py`

**Interfaces:**
- Produces:
  - `build_eval_argv(entry: QueueEntry, bundle_dir: Path, *, python: str) -> list[str]`
  - `build_judge_argv(entry: QueueEntry, bundle_dir: Path, *, python: str) -> list[str]`
  - `classify_step(returncode: int | None, timed_out: bool, artifacts_ok: bool) -> str` → `"timeout"|"failed"|"ok"`

- [ ] **Step 1: Write the failing tests**

```python
import json as _json

def _entry(tmp_path):
    cfg = _write(tmp_path / "c.yaml", "endpoint:\n  base_url: x\nmodels:\n  - id: m\n")
    pack = _write(tmp_path / "p.yaml", "id: pk\n")
    return rq.QueueEntry(config=cfg, pack=pack, model={"id": "a/b"},
                         judge_config=_write(tmp_path / "j.yaml", "model: jm\n"),
                         judge_model="jm")

def test_build_eval_argv(tmp_path):
    e = _entry(tmp_path)
    argv = rq.build_eval_argv(e, tmp_path / "bundle", python="PY")
    assert argv[:4] == ["PY", "-m", "touchstone", "eval"]
    assert "--models-json" in argv
    mj = argv[argv.index("--models-json") + 1]
    assert _json.loads(mj) == [{"id": "a/b", "quant": "", "max_tokens_default": 400,
                                "reasoning_headroom_tokens": 0, "extra_body": {}}]
    assert "--emit-events" in argv
    assert argv[argv.index("--run-dir") + 1] == str(tmp_path / "bundle")

def test_build_judge_argv_with_model_override(tmp_path):
    e = _entry(tmp_path)
    argv = rq.build_judge_argv(e, tmp_path / "bundle", python="PY")
    assert argv[:4] == ["PY", "-m", "touchstone", "judge"]
    assert argv[argv.index("--bundle") + 1] == str(tmp_path / "bundle")
    assert argv[argv.index("--judge-model") + 1] == "jm"

def test_classify_step():
    assert rq.classify_step(None, True, False) == "timeout"
    assert rq.classify_step(1, False, True) == "failed"
    assert rq.classify_step(0, False, False) == "failed"   # exit 0 but no artifacts
    assert rq.classify_step(0, False, True) == "ok"
```

- [ ] **Step 2: Run to verify fail** → `uv run pytest tests/test_runqueue.py -k "argv or classify" -q` → FAIL.

- [ ] **Step 3: Implement**

```python
import json

def build_eval_argv(entry: QueueEntry, bundle_dir: Path, *, python: str) -> list[str]:
    return [
        python, "-m", "touchstone", "eval",
        "--config", str(entry.config),
        "--pack", str(entry.pack),
        "--models-json", json.dumps([entry.model.model_dump()]),
        "--run-dir", str(bundle_dir),
        "--emit-events",
    ]

def build_judge_argv(entry: QueueEntry, bundle_dir: Path, *, python: str) -> list[str]:
    argv = [
        python, "-m", "touchstone", "judge",
        "--bundle", str(bundle_dir),
        "--judge-config", str(entry.judge_config),
        "--emit-events",
    ]
    if entry.judge_model:
        argv += ["--judge-model", entry.judge_model]
    return argv

def classify_step(returncode: int | None, timed_out: bool, artifacts_ok: bool) -> str:
    if timed_out:
        return "timeout"
    if returncode != 0:
        return "failed"
    return "ok" if artifacts_ok else "failed"
```

- [ ] **Step 4: Run to verify pass** → PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff format touchstone/runqueue.py tests/test_runqueue.py && uv run ruff check touchstone/runqueue.py && uv run mypy touchstone/runqueue.py
git add -u && git commit -m "feat(queue): pure eval/judge argv builders + step classifier"
```

---

### Task 4: `EntryResult` + Summary (json + md, pure)

**Files:**
- Modify: `touchstone/runqueue.py`
- Test: `tests/test_runqueue.py`

**Interfaces:**
- Produces:
  - `EntryResult(index, model_id, config, pack, bundle_dir, eval_status, judge_status, eval_seconds, judge_seconds, error="")` (dataclass) + `.as_dict()`
  - `summary_json_obj(spec: QueueSpec, results: list[EntryResult], *, started_iso: str) -> dict`
  - `render_summary_md(spec: QueueSpec, results: list[EntryResult], *, started_iso: str) -> str`
  - statuses: eval/judge ∈ `"ok"|"failed"|"timeout"`, judge additionally `"skipped"`, both `"pending"` before run.

- [ ] **Step 1: Write the failing tests**

```python
def test_summary_json_and_md():
    res = [
        rq.EntryResult(0, "a/b", "c.yaml", "p.yaml", "runs/x", "ok", "ok", 12.0, 34.0),
        rq.EntryResult(1, "c/d", "c.yaml", "p.yaml", "runs/y", "timeout", "skipped", 60.0, 0.0,
                       error="eval exceeded 14400s"),
    ]
    spec = rq.QueueSpec(entries=[])
    obj = rq.summary_json_obj(spec, res, started_iso="2026-06-27T22:00:00")
    assert obj["started"] == "2026-06-27T22:00:00"
    assert obj["entries"][1]["eval_status"] == "timeout"
    assert obj["entries"][1]["judge_status"] == "skipped"
    md = rq.render_summary_md(spec, res, started_iso="2026-06-27T22:00:00")
    assert "a/b" in md and "c/d" in md
    assert "timeout" in md
    assert "runs/x" in md
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement**

```python
@dataclass
class EntryResult:
    index: int
    model_id: str
    config: str
    pack: str
    bundle_dir: str
    eval_status: str = "pending"
    judge_status: str = "pending"
    eval_seconds: float = 0.0
    judge_seconds: float = 0.0
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index, "model_id": self.model_id, "config": self.config,
            "pack": self.pack, "bundle_dir": self.bundle_dir,
            "eval_status": self.eval_status, "judge_status": self.judge_status,
            "eval_seconds": round(self.eval_seconds, 1),
            "judge_seconds": round(self.judge_seconds, 1), "error": self.error,
        }

def summary_json_obj(spec: QueueSpec, results: list[EntryResult], *, started_iso: str) -> dict[str, object]:
    return {
        "started": started_iso,
        "total": len(spec.entries),
        "done": len(results),
        "entries": [r.as_dict() for r in results],
    }

def render_summary_md(spec: QueueSpec, results: list[EntryResult], *, started_iso: str) -> str:
    lines = [
        "# Nacht-Queue Summary",
        "",
        f"**Start:** {started_iso} · **Einträge:** {len(spec.entries)} · **Fertig:** {len(results)}",
        "",
        "| # | Modell | eval | judge | eval s | judge s | Bundle |",
        "|---|--------|------|-------|--------|---------|--------|",
    ]
    for r in results:
        lines.append(
            f"| {r.index} | {r.model_id} | {r.eval_status} | {r.judge_status} | "
            f"{r.eval_seconds:.0f} | {r.judge_seconds:.0f} | `{r.bundle_dir}` |"
        )
    errs = [r for r in results if r.error]
    if errs:
        lines += ["", "## Fehler", ""]
        lines += [f"- **{r.model_id}** (#{r.index}): {r.error}" for r in errs]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run to verify pass** → PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff format touchstone/runqueue.py tests/test_runqueue.py && uv run ruff check touchstone/runqueue.py && uv run mypy touchstone/runqueue.py
git add -u && git commit -m "feat(queue): EntryResult + summary.json/summary.md renderers"
```

---

### Task 5: `entries_to_run` (Resume-Skip, pure) + `resolve_entry`

**Files:**
- Modify: `touchstone/runqueue.py`
- Test: `tests/test_runqueue.py`

**Interfaces:**
- Produces:
  - `ResolvedEntry(reset_command: str, settle: SettleSpec, eval_timeout_s: float, judge_timeout_s: float, cooldown_s: float)`
  - `resolve_entry(entry: QueueEntry, defaults: QueueDefaults) -> ResolvedEntry`
  - `entries_to_run(spec: QueueSpec, prior: list[EntryResult]) -> list[tuple[int, QueueEntry]]` — überspringt Einträge, deren prior-Result „komplett" ist: `eval_status == "ok"` UND `judge_status in {"ok","skipped"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_resolve_entry_uses_overrides(tmp_path):
    e = _entry(tmp_path); e.cooldown_s = 7; e.reset_command = "custom"
    d = rq.QueueDefaults(cooldown_s=1, reset_command="def")
    r = rq.resolve_entry(e, d)
    assert r.cooldown_s == 7 and r.reset_command == "custom"
    assert r.eval_timeout_s == d.step_timeout_s.eval_s   # falls back to default

def test_entries_to_run_skips_completed(tmp_path):
    e0, e1 = _entry(tmp_path), _entry(tmp_path)
    spec = rq.QueueSpec(entries=[e0, e1])
    prior = [rq.EntryResult(0, "a/b", "c", "p", "runs/x", "ok", "ok")]
    todo = rq.entries_to_run(spec, prior)
    assert [i for i, _ in todo] == [1]

def test_entries_to_run_retries_failed(tmp_path):
    e0 = _entry(tmp_path)
    spec = rq.QueueSpec(entries=[e0])
    prior = [rq.EntryResult(0, "a/b", "c", "p", "runs/x", "failed", "skipped")]
    assert [i for i, _ in rq.entries_to_run(spec, prior)] == [0]
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement**

```python
def _pick(v: object | None, d: object) -> object:
    return d if v is None else v

@dataclass
class ResolvedEntry:
    reset_command: str
    settle: SettleSpec
    eval_timeout_s: float
    judge_timeout_s: float
    cooldown_s: float

def resolve_entry(entry: QueueEntry, defaults: QueueDefaults) -> ResolvedEntry:
    st = entry.step_timeout_s or defaults.step_timeout_s
    return ResolvedEntry(
        reset_command=str(_pick(entry.reset_command, defaults.reset_command)),
        settle=entry.settle or defaults.settle,
        eval_timeout_s=st.eval_s,
        judge_timeout_s=st.judge_s,
        cooldown_s=float(_pick(entry.cooldown_s, defaults.cooldown_s)),
    )

def _completed(r: EntryResult) -> bool:
    return r.eval_status == "ok" and r.judge_status in ("ok", "skipped")

def entries_to_run(spec: QueueSpec, prior: list[EntryResult]) -> list[tuple[int, QueueEntry]]:
    done = {r.index for r in prior if _completed(r)}
    return [(i, e) for i, e in enumerate(spec.entries) if i not in done]
```

- [ ] **Step 4: Run to verify pass** → PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff format touchstone/runqueue.py tests/test_runqueue.py && uv run ruff check touchstone/runqueue.py && uv run mypy touchstone/runqueue.py
git add -u && git commit -m "feat(queue): resolve_entry (defaults<-overrides) + entries_to_run resume skip"
```

---

### Task 6: `run_queue` Orchestrierungsschleife (DI)

**Files:**
- Modify: `touchstone/runqueue.py`
- Test: `tests/test_runqueue.py`

**Interfaces:**
- Produces:
  - `StepOutcome(status: str, seconds: float)` (dataclass)
  - `RunStep = Callable[[str, list[str], float, Path], StepOutcome]` (`kind` ∈ `"eval"|"judge"`, argv, timeout_s, bundle_dir)
  - `run_queue(spec, *, queue_dir: Path, output_dir: Path, python: str, run_step: RunStep, reset_run: Callable[[str], bool], ram_poll: Callable[[], float], sleep: Callable[[float], None], clock: Callable[[], float], ts: str, started_iso: str, prior: list[EntryResult] | None = None) -> list[EntryResult]`
- Behaviour: für jeden `entries_to_run`-Eintrag → `reset_run(resolved.reset_command)` (Fehler nur warnen) → `wait_until_settled` → optional `cooldown` (via `sleep`) → `run_step("eval", build_eval_argv(...), eval_timeout_s, bundle)` → bei eval-Status ≠ `"ok"`: judge = `"skipped"` → sonst, falls `judge_config`: `run_step("judge", ...)`, sonst judge `"skipped"`. Nach jedem Eintrag `summary.json`/`summary.md` in `queue_dir` schreiben (kumulativ inkl. `prior`). Gibt die *vollständige* Result-Liste (prior + neu) zurück.

- [ ] **Step 1: Write the failing tests**

```python
def _spec_two(tmp_path, judge=True):
    cfg = _write(tmp_path / "c.yaml", "endpoint:\n  base_url: x\nmodels:\n  - id: m\n")
    pack = _write(tmp_path / "p.yaml", "id: pk\n")
    jc = _write(tmp_path / "j.yaml", "model: jm\n") if judge else None
    def mk(mid):
        return rq.QueueEntry(config=cfg, pack=pack, model={"id": mid},
                             judge_config=jc, judge_model="")
    return rq.QueueSpec(entries=[mk("a/b"), mk("c/d")])

def _const_time():
    t = {"now": 0.0}
    return (lambda: t["now"]), (lambda s: t.__setitem__("now", t["now"] + s)), t

def test_run_queue_happy_path(tmp_path):
    spec = _spec_two(tmp_path)
    calls = []
    def run_step(kind, argv, timeout, bundle):
        calls.append((kind, str(bundle)))
        return rq.StepOutcome("ok", 1.0)
    resets = []
    clock, sleep, _ = _const_time()
    qd = tmp_path / "q"; qd.mkdir()
    res = rq.run_queue(spec, queue_dir=qd, output_dir=tmp_path / "runs", python="PY",
                       run_step=run_step, reset_run=lambda c: (resets.append(c) or True),
                       ram_poll=lambda: 1000.0, sleep=sleep, clock=clock,
                       ts="T", started_iso="ISO")
    assert [r.eval_status for r in res] == ["ok", "ok"]
    assert [r.judge_status for r in res] == ["ok", "ok"]
    assert [c[0] for c in calls] == ["eval", "judge", "eval", "judge"]
    assert len(resets) == 2                       # reset before each entry
    assert (qd / "summary.json").exists() and (qd / "summary.md").exists()

def test_run_queue_eval_fail_skips_judge(tmp_path):
    spec = _spec_two(tmp_path)
    def run_step(kind, argv, timeout, bundle):
        return rq.StepOutcome("failed" if kind == "eval" else "ok", 1.0)
    clock, sleep, _ = _const_time()
    qd = tmp_path / "q"; qd.mkdir()
    res = rq.run_queue(spec, queue_dir=qd, output_dir=tmp_path/"runs", python="PY",
                       run_step=run_step, reset_run=lambda c: True,
                       ram_poll=lambda: 1.0, sleep=sleep, clock=clock, ts="T", started_iso="I")
    assert all(r.eval_status == "failed" for r in res)
    assert all(r.judge_status == "skipped" for r in res)

def test_run_queue_timeout_continues(tmp_path):
    spec = _spec_two(tmp_path)
    seen = []
    def run_step(kind, argv, timeout, bundle):
        seen.append(kind)
        return rq.StepOutcome("timeout" if kind == "eval" else "ok", float(timeout))
    clock, sleep, _ = _const_time()
    qd = tmp_path / "q"; qd.mkdir()
    res = rq.run_queue(spec, queue_dir=qd, output_dir=tmp_path/"runs", python="PY",
                       run_step=run_step, reset_run=lambda c: True,
                       ram_poll=lambda: 1.0, sleep=sleep, clock=clock, ts="T", started_iso="I")
    assert [r.eval_status for r in res] == ["timeout", "timeout"]
    assert seen.count("eval") == 2                # second entry still attempted

def test_run_queue_no_judge_when_no_judge_config(tmp_path):
    spec = _spec_two(tmp_path, judge=False)
    res = rq.run_queue(spec, queue_dir=(tmp_path/"q"), output_dir=tmp_path/"runs", python="PY",
                       run_step=lambda *a: rq.StepOutcome("ok", 1.0), reset_run=lambda c: True,
                       ram_poll=lambda: 1.0, sleep=lambda s: None, clock=lambda: 0.0,
                       ts="T", started_iso="I")
    assert all(r.judge_status == "skipped" for r in res)

def test_run_queue_resumes_prior(tmp_path):
    spec = _spec_two(tmp_path)
    prior = [rq.EntryResult(0, "a/b", "c", "p", "runs/x", "ok", "ok")]
    seen = []
    rq.run_queue(spec, queue_dir=(tmp_path/"q"), output_dir=tmp_path/"runs", python="PY",
                 run_step=lambda kind, *a: (seen.append(kind) or rq.StepOutcome("ok", 1.0)),
                 reset_run=lambda c: True, ram_poll=lambda: 1.0, sleep=lambda s: None,
                 clock=lambda: 0.0, ts="T", started_iso="I", prior=prior)
    assert seen == ["eval", "judge"]              # only entry 1 ran
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement**

```python
import json as _json2  # if json already imported, reuse it; keep one import at module top

StepOutcome = None  # placeholder removed below

@dataclass
class StepOutcome:
    status: str
    seconds: float

def _write_summary(queue_dir: Path, spec: QueueSpec, results: list[EntryResult], started_iso: str) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "summary.json").write_text(
        json.dumps(summary_json_obj(spec, results, started_iso=started_iso), indent=2,
                   ensure_ascii=False), encoding="utf-8")
    (queue_dir / "summary.md").write_text(
        render_summary_md(spec, results, started_iso=started_iso), encoding="utf-8")

def run_queue(
    spec: QueueSpec,
    *,
    queue_dir: Path,
    output_dir: Path,
    python: str,
    run_step: "Callable[[str, list[str], float, Path], StepOutcome]",
    reset_run: Callable[[str], bool],
    ram_poll: Callable[[], float],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    ts: str,
    started_iso: str,
    prior: list[EntryResult] | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> list[EntryResult]:
    results: list[EntryResult] = list(prior or [])
    pack_id_cache: dict[Path, str] = {}
    for i, entry in entries_to_run(spec, results):
        rv = resolve_entry(entry, spec.defaults)
        # 1. reset + settle (clean RAM baseline before each eval)
        if rv.reset_command:
            if not reset_run(rv.reset_command):
                log(f"⚠ reset failed (entry {i}); continuing")
        wait_until_settled(ram_poll, sleep, clock, settle=rv.settle)
        if rv.cooldown_s > 0:
            sleep(rv.cooldown_s)
        # 2. eval
        bundle = output_dir / run_dir_for(ts, entry.model.id, _pack_id(entry.pack, pack_id_cache))
        r = EntryResult(i, entry.model.id, str(entry.config), str(entry.pack), str(bundle))
        ev = run_step("eval", build_eval_argv(entry, bundle, python=python), rv.eval_timeout_s, bundle)
        r.eval_status, r.eval_seconds = ev.status, ev.seconds
        # 3. judge (only if eval ok and judge requested)
        if ev.status != "ok":
            r.judge_status = "skipped"
            r.error = f"eval {ev.status} (timeout={rv.eval_timeout_s:.0f}s)" if ev.status == "timeout" else "eval failed"
        elif entry.judge_config is None:
            r.judge_status = "skipped"
        else:
            jv = run_step("judge", build_judge_argv(entry, bundle, python=python), rv.judge_timeout_s, bundle)
            r.judge_status, r.judge_seconds = jv.status, jv.seconds
            if jv.status != "ok" and not r.error:
                r.error = f"judge {jv.status}"
        results.append(r)
        _write_summary(queue_dir, spec, results, started_iso)
    return results

def _pack_id(pack: Path, cache: dict[Path, str]) -> str:
    if pack not in cache:
        try:
            data = yaml.safe_load(pack.read_text(encoding="utf-8")) or {}
            cache[pack] = str(data.get("id") or pack.stem)
        except Exception:
            cache[pack] = pack.stem
    return cache[pack]
```

Note: remove the `StepOutcome = None` placeholder line; only the `@dataclass StepOutcome` remains. Ensure a single `import json` at module top (Task 3 added it).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_runqueue.py -q` → PASS (all).

- [ ] **Step 5: Lint/type/commit**

```bash
uv run ruff format touchstone/runqueue.py tests/test_runqueue.py && uv run ruff check touchstone/runqueue.py && uv run mypy touchstone/runqueue.py
git add -u && git commit -m "feat(queue): run_queue orchestration loop (reset→settle→eval→judge, incremental summary, resume)"
```

---

### Task 7: CLI `queue`-Command (echter Subprozess-Spawn + psutil + time)

**Files:**
- Modify: `touchstone/cli.py`
- Modify: `touchstone/runqueue.py` (real `run_step` factory + `reset_run` + `ram_poll`)
- Test: `tests/test_runqueue.py` (test `classify`/spawn-decision via injected fake process)

**Interfaces:**
- Produces in `runqueue.py`:
  - `default_ram_poll() -> float` (`psutil.virtual_memory().used / (1024*1024)`)
  - `run_reset(command: str, *, runner=subprocess.run) -> bool` — `shlex.split`, `runner(...)`, True iff returncode 0; **never raises** (catches `FileNotFoundError`/`OSError` → False)
  - `make_run_step(*, clock, popen=subprocess.Popen, grace_s: float = 10) -> RunStep` — spawns, `wait(timeout)`, on timeout `terminate()`→`wait(grace)`→`kill()`, then `classify_step(rc, timed_out, artifacts_ok)` where artifacts: eval→`responses.jsonl`; judge→`reports.jsonl`+`scores.csv`.
- Produces in `cli.py`: Typer `queue` command (`queue_file: Path`, `--check`, `--resume`).

- [ ] **Step 1: Write failing test for `run_reset` + artifact-aware step classify**

```python
def test_run_reset_handles_missing_binary():
    assert rq.run_reset("definitely-not-a-real-binary-xyz --flags") is False

def test_run_reset_ok(monkeypatch):
    class R: returncode = 0
    assert rq.run_reset("echo hi", runner=lambda *a, **k: R()) is True

def test_make_run_step_timeout_kills(tmp_path):
    import subprocess
    class FakePopen:
        def __init__(self, argv, **k): self.argv = argv; self.returncode = None; self.killed = False
        def wait(self, timeout=None):
            if timeout is not None and not self.killed:
                raise subprocess.TimeoutExpired(self.argv, timeout)
            self.returncode = -9; return -9
        def terminate(self): pass
        def kill(self): self.killed = True
    step = rq.make_run_step(clock=lambda: 0.0, popen=FakePopen, grace_s=0)
    out = step("eval", ["x"], 1.0, tmp_path)
    assert out.status == "timeout"
```

- [ ] **Step 2: Run to verify fail** → FAIL.

- [ ] **Step 3: Implement runqueue helpers**

```python
import shlex
import subprocess

def default_ram_poll() -> float:
    import psutil
    return psutil.virtual_memory().used / (1024 * 1024)

def run_reset(command: str, *, runner: Callable[..., object] = subprocess.run) -> bool:
    if not command.strip():
        return True
    try:
        result = runner(shlex.split(command), capture_output=True, timeout=120)
        return getattr(result, "returncode", 1) == 0
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False

def _artifacts_ok(kind: str, bundle: Path) -> bool:
    if kind == "eval":
        return (bundle / "responses.jsonl").exists()
    return (bundle / "reports.jsonl").exists() and (bundle / "scores.csv").exists()

def make_run_step(*, clock: Callable[[], float], popen=subprocess.Popen, grace_s: float = 10):
    def run_step(kind: str, argv: list[str], timeout_s: float, bundle: Path) -> StepOutcome:
        start = clock()
        proc = popen(argv)
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        rc = proc.returncode
        status = classify_step(rc, timed_out, _artifacts_ok(kind, bundle))
        return StepOutcome(status, clock() - start)
    return run_step
```

- [ ] **Step 4: Run to verify pass** → PASS.

- [ ] **Step 5: Wire the Typer command in `cli.py`**

```python
# add imports near other touchstone imports
import sys
from datetime import datetime
from . import runqueue as rq

@app.command(name="queue")
def queue_cmd(
    queue_file: Path = typer.Option(..., "--queue", "-q", exists=True, help="queue.yaml"),
    check: bool = typer.Option(False, "--check", help="verify the model-switch chain only, no matrix"),
    resume: Path | None = typer.Option(None, "--resume", help="continue an existing runs/<ts>_queue dir"),
) -> None:
    """Run several models overnight, sequentially: per entry reset→settle→eval→[judge]."""
    spec = rq.load_queue(queue_file)
    if check:
        rq.run_check(spec, console=console)   # Task 8
        return
    output_dir = Path("./runs")
    ts = _timestamp()
    if resume is not None:
        queue_dir = resume
        prior = rq.load_prior_results(queue_dir)
        ts = queue_dir.name.replace("_queue", "")
    else:
        queue_dir = output_dir / f"{ts}_queue"
        prior = []
    run_step = rq.make_run_step(clock=time.monotonic)
    console.print(f"[bold]touchstone queue[/] → {queue_dir} ({len(spec.entries)} Einträge)")
    results = rq.run_queue(
        spec, queue_dir=queue_dir, output_dir=output_dir, python=sys.executable,
        run_step=run_step, reset_run=rq.run_reset, ram_poll=rq.default_ram_poll,
        sleep=time.sleep, clock=time.monotonic, ts=ts,
        started_iso=datetime.now().isoformat(timespec="seconds"), prior=prior,
        log=lambda m: console.print(f"[yellow]{m}[/]"),
    )
    ok = sum(1 for r in results if r.eval_status == "ok")
    console.print(f"[green]Queue fertig[/] — {ok}/{len(results)} eval ok · Summary: {queue_dir/'summary.md'}")
```

Add `load_prior_results(queue_dir) -> list[EntryResult]` to `runqueue.py` (reads `summary.json`, tolerant of missing file → `[]`; reconstruct `EntryResult` from each entry dict). Add a test:

```python
def test_load_prior_results_roundtrip(tmp_path):
    qd = tmp_path / "q"; qd.mkdir()
    res = [rq.EntryResult(0, "a/b", "c", "p", "runs/x", "ok", "ok", 1.0, 2.0)]
    rq._write_summary(qd, rq.QueueSpec(entries=[]), res, "ISO")
    back = rq.load_prior_results(qd)
    assert back[0].model_id == "a/b" and back[0].eval_status == "ok"

def test_load_prior_results_missing_is_empty(tmp_path):
    assert rq.load_prior_results(tmp_path / "nope") == []
```

```python
def load_prior_results(queue_dir: Path) -> list[EntryResult]:
    p = Path(queue_dir) / "summary.json"
    if not p.exists():
        return []
    obj = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for e in obj.get("entries", []):
        out.append(EntryResult(
            index=int(e["index"]), model_id=e["model_id"], config=e["config"], pack=e["pack"],
            bundle_dir=e["bundle_dir"], eval_status=e["eval_status"], judge_status=e["judge_status"],
            eval_seconds=float(e["eval_seconds"]), judge_seconds=float(e["judge_seconds"]),
            error=e.get("error", ""),
        ))
    return out
```

- [ ] **Step 6: Run full suite + lint/type**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy touchstone/`
Expected: all PASS. (Resolve any mypy complaints on the Typer command / Callable types.)

- [ ] **Step 7: Commit**

```bash
git add -u && git commit -m "feat(queue): touchstone queue CLI command (real spawn/psutil/time, --resume)"
```

---

### Task 8: `--check` Verify-Mode + `queue.example.yaml` + Docs

**Files:**
- Modify: `touchstone/runqueue.py` (`run_check`)
- Modify: `touchstone/cli.py` (already calls `rq.run_check`)
- Create: `queue.example.yaml`
- Modify: `tests/test_runqueue.py`, `tests/test_config.py` (example validates)
- Modify: `AGENTS.md`

**Interfaces:**
- Produces: `distinct_models(spec) -> list[ModelSpec]` (pure, dedupe by id); `run_check(spec, *, console, make_client=…, reset_run=…, ram_poll=…, sleep=…, clock=…) -> list[dict]` — per distinct model: reset+settle+one tiny `preflight_models` request; records `{model, loaded, content_chars, ram_before, ram_after}`; prints a table; writes nothing destructive.

- [ ] **Step 1: Failing test for `distinct_models` + `run_check` (injected preflight)**

```python
def test_distinct_models_dedupe(tmp_path):
    cfg = _write(tmp_path/"c.yaml", "endpoint:\n  base_url: x\nmodels:\n  - id: m\n")
    pack = _write(tmp_path/"p.yaml", "id: pk\n")
    mk = lambda mid: rq.QueueEntry(config=cfg, pack=pack, model={"id": mid})
    spec = rq.QueueSpec(entries=[mk("a"), mk("a"), mk("b")])
    assert [m.id for m in rq.distinct_models(spec)] == ["a", "b"]
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `distinct_models` + `run_check`**

```python
def distinct_models(spec: QueueSpec) -> list[ModelSpec]:
    seen: set[str] = set()
    out: list[ModelSpec] = []
    for e in spec.entries:
        if e.model.id not in seen:
            seen.add(e.model.id)
            out.append(e.model)
    return out

def run_check(spec, *, console, make_client=None, reset_run=run_reset,
              ram_poll=default_ram_poll, sleep=None, clock=None) -> list[dict[str, object]]:
    import time as _t
    from .client import OpenAIStreamClient
    from .config import load_config
    from .preflight import preflight_models
    sleep = sleep or _t.sleep
    clock = clock or _t.monotonic
    rows: list[dict[str, object]] = []
    # group by config so we build the right client per endpoint
    for e in spec.entries[:1] or []:  # client built per first config; check uses each entry's config
        pass
    for m in distinct_models(spec):
        entry = next(e for e in spec.entries if e.model.id == m.id)
        rv = resolve_entry(entry, spec.defaults)
        reset_run(rv.reset_command)
        before = ram_poll()
        wait_until_settled(ram_poll, sleep, clock, settle=rv.settle)
        cfg = load_config(entry.config)
        client = (make_client or OpenAIStreamClient)(cfg.endpoint.base_url, cfg.endpoint.api_key)
        res = preflight_models(client, [m], budget_for=lambda _m: 16)[0]
        after = ram_poll()
        rows.append({"model": m.id, "loaded": res.status == "ok",
                     "status": res.status, "content_chars": res.text_chars,
                     "ram_before_mb": round(before), "ram_after_mb": round(after),
                     "ram_delta_mb": round(after - before)})
    # print a table
    console.print("\n[bold]Modell-Wechsel-Probe (--check)[/]")
    for r in rows:
        mark = "✓" if r["loaded"] else "✗"
        console.print(f"  {mark} {r['model']:30} status={r['status']:6} "
                      f"ΔRAM={r['ram_delta_mb']:+} MB content={r['content_chars']}")
    return rows
```

Note: confirm `PreflightResult` field name for content chars during impl (`text_chars` vs `tc`); adapt the attribute access. `OpenAIStreamClient` constructor args: confirm via `touchstone/client.py` (`_make_client` in cli.py shows the real call) and mirror it.

- [ ] **Step 4: Run → PASS** (`distinct_models` test; `run_check` is exercised manually + a light smoke with an injected fake `make_client`/`preflight` if feasible).

- [ ] **Step 5: Create `queue.example.yaml`**

```yaml
# Beispiel-Nacht-Queue: zwei Modelle gegen buero, je eval→judge.
# Vor dem ersten echten Lauf:  uv run touchstone queue -q queue.example.yaml --check
defaults:
  reset_command: "lms unload --all"     # zwischen Einträgen; "" = aus
  settle: { timeout_s: 120, plateau_polls: 3, poll_interval_s: 2, epsilon_mb: 200 }
  step_timeout_s: { eval: 14400, judge: 21600 }
  cooldown_s: 0
entries:
  - config: config.m5-lmstudio.yaml
    pack: packs/buero.yaml
    model: { id: "google/gemma-4-12b" }
    judge_config: judge.yaml
  - config: config.m5-lmstudio.yaml
    pack: packs/buero.yaml
    model: { id: "qwen/qwen3.6-27b" }
    judge_config: judge.yaml
```

- [ ] **Step 6: Add validation test (mirror `tests/test_config.py`)**

```python
# tests/test_config.py (or test_runqueue.py)
def test_shipped_queue_example_validates():
    from touchstone import runqueue as rq
    spec = rq.load_queue("queue.example.yaml")
    assert len(spec.entries) >= 1
```

- [ ] **Step 7: Update `AGENTS.md`** — add to the Commands block:

```bash
uv run touchstone queue --queue queue.example.yaml          # Nacht-Queue: mehrere Modelle sequenziell eval→judge
uv run touchstone queue --queue queue.example.yaml --check  # nur die LM-Studio-Modell-Wechsel-Kette verifizieren
uv run touchstone queue --queue queue.example.yaml --resume runs/<ts>_queue  # nach Abbruch weiter
```

And add one Gotcha bullet: „**Die Nacht-Queue erkennt Fertigstellung am Subprozess-Exit, nie an einem Fortschrittsbalken** (die holistische Judge-Phase liefert ~0 Events, läuft aber weiter — „100 % ≠ fertig"). Hänger fängt der per-Step-Watchdog (`step_timeout_s`); zwischen Einträgen `reset_command` + RAM-Settle für saubere Modell-Deltas. Reset ist ein konfigurierbares Shell-Kommando (LM-Studio-`lms unload --all` als Default), kein Engine-Branch."

- [ ] **Step 8: Full suite + lint + type + commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy touchstone/
git add -A && git commit -m "feat(queue): --check verify mode + queue.example.yaml + AGENTS.md docs"
```

---

## Self-Review

**1. Spec coverage:**
- Subprozess-Orchestrator ✓ (Task 6/7) · queue.yaml-Schema ✓ (Task 1) · ein-Modell/Eintrag via `--models-json` ✓ (Task 3) · reset+settle ✓ (Task 2/6/7) · per-Eintrag-Flow + Fertig=Exit+Artefakte ✓ (Task 6 `classify_step` Task 3) · continue-on-error + Watchdog-Timeout ✓ (Task 6/7) · summary.json/md + inkrementell ✓ (Task 4/6) · `--resume` ✓ (Task 5/7) · `--check` ✓ (Task 8) · Module `runqueue.py` + dünner CLI ✓ · Tests DI ✓ · queue.example validates ✓ (Task 8) · AGENTS.md ✓ (Task 8). Koexistenz GUI-Lock = bewusst v2 (Spec sagt „falls billig" — hier ausgelassen, da Sentinel-Schreiben nicht-trivial; im Review erneut prüfen).
**2. Placeholder scan:** Zwei „confirm during impl"-Notizen (PreflightResult-Feldname, OpenAIStreamClient-Ctor) — sind echte Verifikationsschritte gegen existierenden Code, kein vager Platzhalter; in Task 8 Step 3 explizit. `StepOutcome = None`-Platzhalterzeile in Task 6 ist mit Entfernungs-Hinweis versehen.
**3. Type consistency:** `StepOutcome`, `EntryResult`, `SettleSpec`, `ResolvedEntry`, `run_step`-Signatur `(kind, argv, timeout_s, bundle)` durchgängig identisch in Task 3/6/7. `StepTimeouts.eval_s/judge_s` (YAML-alias `eval`/`judge`) konsistent in Task 1/5. `classify_step`-Reihenfolge (timeout→failed→ok) konsistent Task 3/7.

## Verifikation am Ende (vor Merge)

`uv run pytest -q` (alle, inkl. ~25 neue) · `uv run ruff check . && ruff format --check .` · `uv run mypy touchstone/` — alle grün. Dann adversariale Whole-Branch-Review (mehrere Lenses) per Workflow, Findings fixen, `feat/nacht-queue` → `main` mergen + pushen (solo, kein PR).
