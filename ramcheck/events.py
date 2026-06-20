"""Event contract for the live monitor.

The eval loop fires callbacks (run_eval's on_run_start/on_cell_start/on_cell_done);
the CLI's --web wiring turns them into lines of an append-only events.jsonl. The
monitor subprocess reads those lines back and aggregates them with build_view().
Pure: no I/O beyond (de)serialising dicts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field

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
        "ts": ts,
        "type": CELL_START,
        "i": i,
        "model": model,
        "variant": variant,
        "category": category,
        "prompt_id": prompt_id,
        "repeat": repeat,
    }


def cell_done_event(
    ts: float,
    i: int,
    model: str,
    variant: str,
    prompt_id: str,
    repeat: int,
    ok: bool,
    ttft_s: float,
    e2e_s: float,
    decode_tps: float,
    completion_tokens: int,
    content_empty: bool,
    error: str,
    reasoning_chars: int = 0,
) -> dict[str, object]:
    return {
        "ts": ts,
        "type": CELL_DONE,
        "i": i,
        "model": model,
        "variant": variant,
        "prompt_id": prompt_id,
        "repeat": repeat,
        "ok": ok,
        "ttft_s": ttft_s,
        "e2e_s": e2e_s,
        "decode_tps": decode_tps,
        "completion_tokens": completion_tokens,
        "content_empty": content_empty,
        "error": error,
        "reasoning_chars": reasoning_chars,
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
    reasoning_chars: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "i": self.i,
            "model": self.model,
            "variant": self.variant,
            "prompt_id": self.prompt_id,
            "status": self.status,
            "ok": self.ok,
            "ttft_s": self.ttft_s,
            "e2e_s": self.e2e_s,
            "decode_tps": self.decode_tps,
            "content_empty": self.content_empty,
            "error": self.error,
            "reasoning_chars": self.reasoning_chars,
        }


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
            "total": self.total,
            "done": self.done,
            "ok": self.ok,
            "failed": self.failed,
            "eta_s": self.eta_s,
            "finished": self.finished,
            "running": [c.as_dict() for c in self.running],
            "cells": [c.as_dict() for c in self.cells],
        }


def _key(ev: dict[str, object]) -> CellKey:
    return (str(ev["model"]), str(ev["variant"]), str(ev["prompt_id"]), _as_int(ev["repeat"]))


def _as_int(x: object, default: int = 0) -> int:
    return int(x) if isinstance(x, (int, float, str)) else default


def _optf(x: object) -> float | None:
    return float(x) if isinstance(x, (int, float)) else None


def _optb(x: object) -> bool | None:
    return bool(x) if isinstance(x, bool) else None


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
            total = max(total, _as_int(e.get("total", 0)))
        elif t == RUN_DONE:
            finished = True
        elif t == CELL_START:
            k = _key(e)
            if k not in by_key:
                order.append(k)
            if by_key.get(k) is None or by_key[k].status != "done":
                by_key[k] = CellView(
                    key=k,
                    i=_as_int(e.get("i", -1), -1),
                    model=str(e["model"]),
                    variant=str(e["variant"]),
                    prompt_id=str(e["prompt_id"]),
                    status="running",
                )
        elif t == CELL_DONE:
            k = _key(e)
            if k not in by_key:
                order.append(k)
            by_key[k] = CellView(
                key=k,
                i=_as_int(e.get("i", -1), -1),
                model=str(e["model"]),
                variant=str(e["variant"]),
                prompt_id=str(e["prompt_id"]),
                status="done",
                ok=bool(e.get("ok")),
                ttft_s=_optf(e.get("ttft_s")),
                e2e_s=_optf(e.get("e2e_s")),
                decode_tps=_optf(e.get("decode_tps")),
                content_empty=_optb(e.get("content_empty")),
                error=str(e.get("error", "")),
                reasoning_chars=_as_int(e.get("reasoning_chars", 0)) or 0,
            )
    cells = [by_key[k] for k in order]
    done_cells = [c for c in cells if c.status == "done"]
    done = len(done_cells)
    ok = sum(1 for c in done_cells if c.ok)
    running = [c for c in cells if c.status == "running"]
    e2es = [c.e2e_s for c in done_cells if c.e2e_s is not None]
    eta_s = (sum(e2es) / len(e2es)) * (total - done) if (e2es and total > done) else None
    return RunView(
        total=total,
        done=done,
        ok=ok,
        failed=done - ok,
        running=running,
        cells=cells,
        eta_s=eta_s,
        finished=finished,
    )
