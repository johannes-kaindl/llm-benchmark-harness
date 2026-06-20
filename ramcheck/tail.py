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
