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
