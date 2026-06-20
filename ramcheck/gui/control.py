# ramcheck/gui/control.py
"""Out-of-process control-plane for the GUI: a run-sentinel (run.json) + a registry that
spawns/stops ramcheck measurement subprocesses and enforces one-run-at-a-time.

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
