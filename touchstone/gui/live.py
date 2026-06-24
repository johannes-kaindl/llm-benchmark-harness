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
        prev_offset = self._offset
        lines, self._offset = tail.read_new(self.events_path, self._offset)
        if self._offset < prev_offset:
            # Truncation/rotation (e.g. a fresh judge --web run rewrote the file):
            # tail reset the byte offset, so drop the stale events before appending
            # the freshly-read ones — otherwise the old (larger) total sticks.
            self._events.clear()
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
