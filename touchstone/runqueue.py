"""Nacht-Queue / Daisy-Chain orchestration: run several models overnight, sequentially.

Per entry: reset endpoint -> wait for RAM to settle -> ``eval`` (subprocess) -> optional
``judge`` (subprocess) -> record. One model per entry (fresh RAM baseline). Pure logic here
(schema, settle-wait, argv builders, summary, resume, step classification) with the real
subprocess/psutil/time wiring injected by the ``queue`` CLI command — so it stays testable
without a live server or sudo, like ``stream_once(clock=…)`` elsewhere in the repo.

Named ``runqueue`` (not ``queue``) so it never shadows the stdlib ``queue`` module.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .config import ModelSpec


# ----------------------------------------------------------------------- schema
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
    # optional per-entry overrides of the matching ``defaults`` key
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
    entries: list[QueueEntry] = Field(default_factory=list)


def load_queue(path: str | Path) -> QueueSpec:
    """Parse + validate a queue.yaml. Raises ``ValueError`` on bad YAML, no entries, or a
    referenced ``config``/``pack``/``judge_config`` file that does not exist."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        spec = QueueSpec.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"invalid queue.yaml: {e}") from e
    if not spec.entries:
        raise ValueError("queue.yaml has no entries")
    seen: set[tuple[str, str, str]] = set()
    for i, entry in enumerate(spec.entries):
        if not entry.config.exists():
            raise ValueError(f"entry {i}: config not found: {entry.config}")
        if not entry.pack.exists():
            raise ValueError(f"entry {i}: pack not found: {entry.pack}")
        if entry.judge_config is not None and not entry.judge_config.exists():
            raise ValueError(f"entry {i}: judge_config not found: {entry.judge_config}")
        # (config, pack, model) must be unique — two equal entries map to the same bundle dir
        # and would silently overwrite each other mid-night.
        key = (str(entry.config), str(entry.pack), entry.model.id)
        if key in seen:
            raise ValueError(f"entry {i}: duplicate (config, pack, model): {key}")
        seen.add(key)
    return spec


# ----------------------------------------------------------------- run-dir slug
def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")


def run_dir_for(ts: str, model_id: str, pack_id: str, index: int) -> str:
    """Deterministic, collision-free bundle dir name. The entry ``index`` disambiguates so two
    distinct entries that slugify equally (e.g. ``m:q4`` vs ``m-q4``) or share model+pack across
    different configs never map to the same dir and silently overwrite each other."""
    return f"{ts}_e{index}_{_slug(model_id)}_eval_{_slug(pack_id)}"


# ----------------------------------------------------------------- settle-wait
@dataclass
class SettleOutcome:
    settled: bool  # True = RAM plateau reached; False = timed out
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
    """Poll system RAM until it plateaus (``|Δ| < epsilon_mb`` for ``plateau_polls`` polls in
    a row) or ``timeout_s`` elapses. Condition-based, not a fixed sleep — so a freshly unloaded
    endpoint's baseline tick lands on the settled (low) state. ``sleep`` advances ``clock`` in
    tests."""
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


# ----------------------------------------------- argv builders + step classifier
def build_eval_argv(entry: QueueEntry, bundle_dir: Path, *, python: str) -> list[str]:
    """``python -m touchstone eval`` argv for one entry (exactly one model via --models-json).
    Always ``--run-dir`` (a fresh run), NEVER ``eval --resume``: eval drops ``--models-json`` on
    resume (the model override only applies on a non-resume start), so a per-eval ``--resume``
    would rebuild the matrix from ``config.models`` — the wrong model — and corrupt the bundle.
    Queue resume is therefore entry-granular: an incomplete eval is re-run from scratch."""
    return [
        python,
        "-m",
        "touchstone",
        "eval",
        "--config",
        str(entry.config),
        "--pack",
        str(entry.pack),
        "--models-json",
        json.dumps([entry.model.model_dump()]),
        "--run-dir",
        str(bundle_dir),
        "--emit-events",
    ]


def build_judge_argv(entry: QueueEntry, bundle_dir: Path, *, python: str) -> list[str]:
    """``python -m touchstone judge`` argv for one entry's bundle (judge_config must be set)."""
    argv = [
        python,
        "-m",
        "touchstone",
        "judge",
        "--bundle",
        str(bundle_dir),
        "--judge-config",
        str(entry.judge_config),
        "--emit-events",
    ]
    if entry.judge_model:
        argv += ["--judge-model", entry.judge_model]
    return argv


def classify_step(returncode: int | None, timed_out: bool, artifacts_ok: bool) -> str:
    """A step is ``timeout`` if killed, ``failed`` on non-zero exit or missing finalize
    artifacts (exit 0 alone is not enough), else ``ok``."""
    if timed_out:
        return "timeout"
    if returncode != 0:
        return "failed"
    return "ok" if artifacts_ok else "failed"


# ------------------------------------------------------- per-entry result + summary
@dataclass
class EntryResult:
    index: int
    model_id: str
    config: str
    pack: str
    bundle_dir: str
    eval_status: str = "pending"  # "ok" | "failed" | "timeout" | "pending"
    judge_status: str = "pending"  # + "skipped"
    eval_seconds: float = 0.0
    judge_seconds: float = 0.0
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "model_id": self.model_id,
            "config": self.config,
            "pack": self.pack,
            "bundle_dir": self.bundle_dir,
            "eval_status": self.eval_status,
            "judge_status": self.judge_status,
            "eval_seconds": round(self.eval_seconds, 1),
            "judge_seconds": round(self.judge_seconds, 1),
            "error": self.error,
        }


def summary_json_obj(
    spec: QueueSpec, results: list[EntryResult], *, started_iso: str
) -> dict[str, object]:
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
        f"**Start:** {started_iso} · **Einträge:** {len(spec.entries)} · "
        f"**Fertig:** {len(results)}",
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


# ------------------------------------------ effective entry + resume skip-logic
def _pick[T](v: T | None, d: T) -> T:
    return d if v is None else v


@dataclass
class ResolvedEntry:
    reset_command: str
    settle: SettleSpec
    eval_timeout_s: float
    judge_timeout_s: float
    cooldown_s: float


def resolve_entry(entry: QueueEntry, defaults: QueueDefaults) -> ResolvedEntry:
    """Flatten a queue entry against the queue defaults (per-entry keys win when set)."""
    st = entry.step_timeout_s or defaults.step_timeout_s
    return ResolvedEntry(
        reset_command=_pick(entry.reset_command, defaults.reset_command),
        settle=entry.settle or defaults.settle,
        eval_timeout_s=st.eval_s,
        judge_timeout_s=st.judge_s,
        cooldown_s=_pick(entry.cooldown_s, defaults.cooldown_s),
    )


def _completed(r: EntryResult) -> bool:
    return r.eval_status == "ok" and r.judge_status in ("ok", "skipped")


def entries_to_run(spec: QueueSpec, prior: list[EntryResult]) -> list[tuple[int, QueueEntry]]:
    """Indices+entries still to run: skip those whose prior result is complete (eval ok AND
    judge ok/skipped); retry failed/timeout/partial ones."""
    done = {r.index for r in prior if _completed(r)}
    return [(i, e) for i, e in enumerate(spec.entries) if i not in done]


# ----------------------------------------------------- orchestration loop (DI)
@dataclass
class StepOutcome:
    status: str  # "ok" | "failed" | "timeout"
    seconds: float


RunStep = Callable[[str, list[str], float, Path], StepOutcome]


def _pack_id(pack: Path, cache: dict[Path, str]) -> str:
    if pack not in cache:
        try:
            data = yaml.safe_load(pack.read_text(encoding="utf-8")) or {}
            cache[pack] = str(data.get("id") or pack.stem)
        except Exception:
            cache[pack] = pack.stem
    return cache[pack]


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + ``os.replace`` so a crash mid-write can never leave a truncated
    file (resume reads summary.json — a half-written one would otherwise break it)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_summary(
    queue_dir: Path, spec: QueueSpec, results: list[EntryResult], started_iso: str
) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        queue_dir / "summary.json",
        json.dumps(
            summary_json_obj(spec, results, started_iso=started_iso), indent=2, ensure_ascii=False
        ),
    )
    _atomic_write(
        queue_dir / "summary.md", render_summary_md(spec, results, started_iso=started_iso)
    )


def run_queue(
    spec: QueueSpec,
    *,
    queue_dir: Path,
    output_dir: Path,
    python: str,
    run_step: RunStep,
    reset_run: Callable[[str], bool],
    ram_poll: Callable[[], float],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    ts: str,
    started_iso: str,
    prior: list[EntryResult] | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> list[EntryResult]:
    """Sequentially run each not-yet-completed entry: reset endpoint -> wait for RAM to settle
    -> eval (subprocess via ``run_step``) -> optional judge -> record. Continue on any error;
    write ``summary.json``/``summary.md`` after each entry. Returns prior + new results."""
    results: list[EntryResult] = list(prior or [])
    todo = entries_to_run(spec, results)
    rerun = {i for i, _ in todo}
    # resume must REPLACE a retried entry's stale row, not append a duplicate (else done > total);
    # also drop any prior row whose index is out of the current spec (queue.yaml shrank → no entry).
    n = len(spec.entries)
    results = [r for r in results if r.index not in rerun and r.index < n]
    pack_cache: dict[Path, str] = {}
    for i, entry in todo:
        rv = resolve_entry(entry, spec.defaults)
        # 1. reset + settle -> clean RAM baseline before this entry's eval
        if rv.reset_command and not reset_run(rv.reset_command):
            log(f"⚠ reset failed (entry {i}: {rv.reset_command!r}); continuing")
        settled = wait_until_settled(ram_poll, sleep, clock, settle=rv.settle)
        if not settled.settled:
            log(
                f"⚠ settle timed out after {settled.waited_s:.0f}s (entry {i}); "
                f"RAM {settled.final_mb:.0f}MB — baseline may be dirty, continuing"
            )
        if rv.cooldown_s > 0:
            sleep(rv.cooldown_s)
        # 2. eval. NOTE: queue-spawned eval/judge run as plain CLI subprocesses and do NOT take the
        # GUI one-run sentinel lock (RunRegistry) — coexistence with a hand-triggered GUI run is a
        # deferred v2 concern (see spec "Koexistenz mit der GUI-One-Run-Lock"). A retried entry
        # re-runs its eval from scratch (fresh --run-dir) — see build_eval_argv for why per-eval
        # --resume is unsafe here (it would drop the model override).
        bundle = output_dir / run_dir_for(ts, entry.model.id, _pack_id(entry.pack, pack_cache), i)
        r = EntryResult(i, entry.model.id, str(entry.config), str(entry.pack), str(bundle))
        ev = run_step(
            "eval", build_eval_argv(entry, bundle, python=python), rv.eval_timeout_s, bundle
        )
        r.eval_status, r.eval_seconds = ev.status, ev.seconds
        # 3. judge (only if eval ok and a judge_config is set)
        if ev.status != "ok":
            r.judge_status = "skipped"
            r.error = (
                f"eval timeout (>{rv.eval_timeout_s:.0f}s)"
                if ev.status == "timeout"
                else "eval failed"
            )
        elif entry.judge_config is None:
            r.judge_status = "skipped"
        else:
            jv = run_step(
                "judge", build_judge_argv(entry, bundle, python=python), rv.judge_timeout_s, bundle
            )
            r.judge_status, r.judge_seconds = jv.status, jv.seconds
            if jv.status != "ok":
                r.error = f"judge {jv.status}"
        results.append(r)
        results.sort(key=lambda x: x.index)
        _write_summary(queue_dir, spec, results, started_iso)
    return results


# ----------------------------------- real wiring (subprocess / psutil / resume)
def default_ram_poll() -> float:
    """System used memory in MB (mirrors sampler.sample_once's sys_used_mb)."""
    import psutil

    return psutil.virtual_memory().used / (1024 * 1024)


def run_reset(command: str, *, runner: Callable[..., object] = subprocess.run) -> bool:
    """Run the between-entries reset command (e.g. ``lms unload --all``). True iff it exited 0.
    An empty command is a no-op (True). Never raises — a missing binary / OS error → False."""
    if not command.strip():
        return True
    try:
        result = runner(shlex.split(command), capture_output=True, timeout=120)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False
    return getattr(result, "returncode", 1) == 0


def _artifacts_ok(kind: str, bundle: Path) -> bool:
    if kind == "eval":
        return (bundle / "responses.jsonl").exists()
    return (bundle / "reports.jsonl").exists() and (bundle / "scores.csv").exists()


class _Proc(Protocol):
    returncode: int | None

    def wait(self, timeout: float | None = ...) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


def make_run_step(
    *,
    clock: Callable[[], float],
    popen: Callable[..., _Proc] = subprocess.Popen,
    grace_s: float = 10,
) -> RunStep:
    """Build the real ``run_step``: spawn the argv, wait up to ``timeout_s``; on timeout escalate
    terminate -> (grace) -> kill; classify by exit code + finalize artifacts. Completion is the
    process EXIT (+ artifacts), never a progress bar — so the holistic judge phase (which emits
    no events) can't be mistaken for 'done' or 'hung'."""

    def run_step(kind: str, argv: list[str], timeout_s: float, bundle: Path) -> StepOutcome:
        start = clock()
        try:
            proc = popen(argv)
        except (OSError, ValueError):
            # spawn-time failure (bad executable, ENOMEM/EAGAIN, EMFILE) -> failed entry, never
            # propagate: the unattended queue must keep going (continue-on-error).
            return StepOutcome("failed", clock() - start)
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
                # bounded reap: a D-state process won't die even on SIGKILL; don't block the
                # whole night on the reap — leak the zombie (one slot) rather than hang forever.
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=grace_s)
        status = classify_step(proc.returncode, timed_out, _artifacts_ok(kind, bundle))
        return StepOutcome(status, clock() - start)

    return run_step


def distinct_models(spec: QueueSpec) -> list[ModelSpec]:
    """The unique models across all entries, first-seen order (for ``--check``)."""
    seen: set[str] = set()
    out: list[ModelSpec] = []
    for e in spec.entries:
        if e.model.id not in seen:
            seen.add(e.model.id)
            out.append(e.model)
    return out


def _default_probe(entry: QueueEntry, model: ModelSpec) -> tuple[bool, str, int]:
    """Send one tiny request to the entry's endpoint to force a JIT load; returns
    (loaded, status, content_chars)."""
    from .client import OpenAIStreamClient
    from .config import load_config
    from .preflight import preflight_models

    cfg = load_config(entry.config)
    client = OpenAIStreamClient(cfg.endpoint.base_url, cfg.endpoint.api_key)
    res = preflight_models(client, [model], budget_for=lambda _m: 16)[0]
    return res.status == "ok", res.status, res.text_chars


def run_check(
    spec: QueueSpec,
    *,
    reset_run: Callable[[str], bool] = run_reset,
    ram_poll: Callable[[], float] = default_ram_poll,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
    probe: Callable[[QueueEntry, ModelSpec], tuple[bool, str, int]] | None = None,
    emit: Callable[[str], None] = print,
    check_dir: str | Path | None = None,
) -> list[dict[str, object]]:
    """Verify the model-switch chain before a real overnight run: per distinct model, reset +
    settle + one tiny request, recording load/evict + RAM before/after. No matrix, no judge.
    When ``check_dir`` is given, persist ``check.md`` + ``check.json`` (the spec's on-disk record)."""
    import time as _t

    sleep = sleep or _t.sleep
    clock = clock or _t.monotonic
    probe = probe or _default_probe
    rows: list[dict[str, object]] = []
    for model in distinct_models(spec):
        entry = next(e for e in spec.entries if e.model.id == model.id)
        rv = resolve_entry(entry, spec.defaults)
        reset_run(rv.reset_command)
        before = ram_poll()
        wait_until_settled(ram_poll, sleep, clock, settle=rv.settle)
        loaded, status, content = probe(entry, model)
        after = ram_poll()
        rows.append(
            {
                "model": model.id,
                "loaded": loaded,
                "status": status,
                "content_chars": content,
                "ram_before_mb": round(before),
                "ram_after_mb": round(after),
                "ram_delta_mb": round(after - before),
            }
        )
        mark = "✓" if loaded else "✗"
        emit(
            f"  {mark} {model.id}  status={status}  ΔRAM={after - before:+.0f} MB  content={content}"
        )
    if check_dir is not None:
        _write_check(Path(check_dir), rows)
    return rows


def _write_check(check_dir: Path, rows: list[dict[str, object]]) -> None:
    check_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Nacht-Queue `--check` — Modell-Wechsel-Probe",
        "",
        "| Modell | geladen | status | content | ΔRAM (MB) |",
        "|--------|---------|--------|---------|-----------|",
    ]
    for r in rows:
        mark = "✓" if r["loaded"] else "✗"
        lines.append(
            f"| {r['model']} | {mark} | {r['status']} | {r['content_chars']} | {r['ram_delta_mb']} |"
        )
    _atomic_write(check_dir / "check.md", "\n".join(lines) + "\n")
    _atomic_write(check_dir / "check.json", json.dumps(rows, indent=2, ensure_ascii=False))


def load_prior_results(queue_dir: str | Path) -> list[EntryResult]:
    """Reconstruct EntryResults from a queue dir's ``summary.json`` (for ``--resume``).
    Missing OR corrupt/half-written file → empty list (resume degrades to 'rerun all', never
    crashes on the very partial-write a crashed night produces)."""
    p = Path(queue_dir) / "summary.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[EntryResult] = []
    for e in obj.get("entries", []):
        out.append(
            EntryResult(
                index=int(e["index"]),
                model_id=e["model_id"],
                config=e["config"],
                pack=e["pack"],
                bundle_dir=e["bundle_dir"],
                eval_status=e["eval_status"],
                judge_status=e["judge_status"],
                eval_seconds=float(e["eval_seconds"]),
                judge_seconds=float(e["judge_seconds"]),
                error=e.get("error", ""),
            )
        )
    return out
