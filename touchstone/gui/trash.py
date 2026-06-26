"""Soft-delete for run dirs: move them into ``runs/.trash/`` instead of an irreversible rm.

Pure path machinery (move/restore/list), confined to runs_dir and collision-safe — so a
mis-click never destroys an expensive, judged run. The overview's batch-delete moves here;
the trash view restores or purges. ``.trash`` is excluded from discovery (a dot-dir).
"""

from __future__ import annotations

import shutil
from pathlib import Path

TRASH_DIRNAME = ".trash"


def trash_dir(runs_dir: Path) -> Path:
    return runs_dir / TRASH_DIRNAME


def _confined_under(path: Path, base: Path) -> Path:
    """Resolve path and confirm it lives under base (following symlinks); ValueError on escape."""
    real = path.resolve()
    if not real.is_relative_to(base.resolve()):
        raise ValueError(f"path {path} escapes {base}")
    return real


def _collision_free(dest: Path) -> Path:
    """``dest`` or, if it exists, ``dest__2``/``dest__3``/… (mirrors import_bundle naming)."""
    if not dest.exists():
        return dest
    n = 2
    while True:
        candidate = dest.with_name(f"{dest.name}__{n}")
        if not candidate.exists():
            return candidate
        n += 1


def move_to_trash(run_dir: Path, runs_dir: Path) -> Path:
    """Move ``run_dir`` → ``runs_dir/.trash/<name>`` (collision-safe). Returns the destination.

    ``run_dir`` must resolve under ``runs_dir`` (never trash something outside it)."""
    _confined_under(run_dir, runs_dir)
    td = trash_dir(runs_dir)
    td.mkdir(parents=True, exist_ok=True)
    dest = _collision_free(td / run_dir.name)
    shutil.move(str(run_dir), str(dest))
    return dest


def restore_from_trash(name: str, runs_dir: Path) -> Path:
    """Move ``runs_dir/.trash/<name>`` back to ``runs_dir/<name>`` (collision-safe)."""
    td = trash_dir(runs_dir)
    src = _confined_under(td / name, td)
    if not src.is_dir():
        raise ValueError(f"no trashed run {name!r}")
    # Confine the destination too (symmetric with src) — defensive against future callers,
    # though runs_dir/<basename> is inherently under runs_dir.
    dest = _confined_under(_collision_free(runs_dir / Path(name).name), runs_dir)
    shutil.move(str(src), str(dest))
    return dest


def list_trash(runs_dir: Path) -> list[str]:
    """Sorted names of the trashed run dirs (or [] when the trash is empty/absent)."""
    td = trash_dir(runs_dir)
    if not td.is_dir():
        return []
    return sorted(p.name for p in td.iterdir() if p.is_dir())


def purge_trash(runs_dir: Path) -> None:
    """Permanently delete the trash dir (and everything in it). Operates ONLY on
    ``runs_dir/.trash`` — never on ``runs_dir`` itself or anything outside it."""
    td = trash_dir(runs_dir)
    # Confine: td must be exactly runs_dir/.trash, nothing resolved outside it.
    if td.resolve() != (runs_dir.resolve() / TRASH_DIRNAME):
        raise ValueError("refusing to purge a non-confined trash dir")
    if td.is_dir():
        shutil.rmtree(td)
