"""FastAPI control-center app factory (implementation in Task 9+).

This stub satisfies the lazy-import in `ramcheck gui` so the command resolves
when the [gui] extra is installed; `serve` is fully implemented in Task 11."""

from __future__ import annotations

from pathlib import Path


def serve(*, runs_dir: Path, port: int = 0, open_browser: bool = True) -> None:  # pragma: no cover
    """Start the uvicorn server for the GUI control-center (implemented in Task 11)."""
    raise NotImplementedError("GUI server not yet implemented — coming in Task 11")
