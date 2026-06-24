# touchstone/gui/control.py
"""Out-of-process control-plane for the GUI: a run-sentinel (run.json) + a registry that
spawns/stops touchstone measurement subprocesses and enforces one-run-at-a-time.

The sentinel is transient steuer-state, NOT measurement truth (runs/ stays SSOT). It lives
in the active run dir and triples as: (1) the run_dir handle, (2) a cross-process lock that
survives a GUI restart, (3) a discovery anchor for running/crashed runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from touchstone.config import ModelSpec

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
    sentinel_path(run_dir).write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def set_sentinel_pid(run_dir: Path, pid: int) -> None:
    """Patch the pid on an existing sentinel (it was written pre-spawn with a placeholder)."""
    s = read_sentinel(run_dir)
    if s is None:
        return
    s["pid"] = pid
    sentinel_path(run_dir).write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


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


class RunInProgress(RuntimeError):
    """Raised when a start is attempted while a measurement run is already active."""


@dataclass
class RunHandle:
    kind: str  # "eval" | "judge"
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
        # Serializes guard+reserve+sentinel+spawn within this process. FastAPI runs sync
        # handlers in a threadpool, so without this two concurrent starts can both pass
        # the guard before either writes the sentinel (TOCTOU). The on-disk sentinel
        # still provides the cross-process / GUI-restart lock.
        self._lock = threading.Lock()

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
    def start_eval(
        self,
        *,
        pack_path: str,
        config_path: str,
        resume_dir: Path | None = None,
        models: list[ModelSpec] | None = None,
    ) -> RunHandle:
        # Hold the lock across guard+reserve+sentinel+spawn so no window opens between
        # the guard and the on-disk lock being written (TOCTOU).
        with self._lock:
            self._guard_free()
            run_dir = resume_dir or self._new_run_dir(pack_path)
            argv = [
                "eval",
                "--pack",
                pack_path,
                "--config",
                config_path,
                "--run-dir",
                str(run_dir),
                "--emit-events",
            ]
            if resume_dir is not None:
                argv += ["--resume", str(resume_dir)]
            if models:
                argv += ["--models-json", json.dumps([m.model_dump() for m in models])]
            # Write the sentinel (state='running') BEFORE spawn so the on-disk lock
            # exists before any window opens; patch the real pid in post-spawn.
            write_sentinel(
                run_dir, kind="eval", pid=-1, pack_path=pack_path, config_path=config_path
            )
            pid = self.launcher.spawn(argv)
            set_sentinel_pid(run_dir, pid)
            return RunHandle("eval", run_dir, pid)

    def start_judge(
        self, *, bundle: Path, judge_config_path: str, judge_model: str = ""
    ) -> RunHandle:
        with self._lock:
            self._guard_free()
            argv = [
                "judge",
                "--bundle",
                str(bundle),
                "--judge-config",
                judge_config_path,
                "--emit-events",
            ]
            if judge_model.strip():
                argv += ["--judge-model", judge_model.strip()]
            write_sentinel(
                bundle, kind="judge", pid=-1, pack_path="", config_path=judge_config_path
            )
            pid = self.launcher.spawn(argv)
            set_sentinel_pid(bundle, pid)
            return RunHandle("judge", bundle, pid)

    def _new_run_dir(self, pack_path: str) -> Path:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        try:
            from touchstone.pack import load_pack

            pk = load_pack(pack_path)
            pack_id = pk.id
        except Exception:
            # fallback: derive from pack filename (supports fake paths in tests)
            pack_id = Path(pack_path).stem
        return self.runs_dir / f"{ts}_eval_{pack_id}"

    # ---- lifecycle ----
    def poll(self, handle: RunHandle) -> str:
        if self.launcher.alive(handle.pid):
            return "running"
        s = read_sentinel(handle.run_dir)
        state = s.get("state") if s else None
        if state in {"stopped", "failed"}:
            return str(state)
        # pid is dead and state is still 'running'. Distinguish a clean finish from a
        # crash (e.g. kill -9): a finalized run leaves bundle.json/responses.jsonl;
        # without either, the run never finalized → it failed (mirrors bundles.classify,
        # which reports 'crashed' for the same on-disk shape).
        finalized = (handle.run_dir / "bundle.json").exists() or (
            handle.run_dir / "responses.jsonl"
        ).exists()
        if not finalized:
            mark_sentinel(handle.run_dir, "failed")
            return "failed"
        mark_sentinel(handle.run_dir, "finished")
        return "finished"

    def stop(self, handle: RunHandle) -> None:
        self.launcher.terminate(handle.pid)
        mark_sentinel(handle.run_dir, "stopped")
