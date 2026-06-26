from __future__ import annotations

import pytest

from touchstone.gui import trash


def _mkrun(runs, name):
    d = runs / name
    d.mkdir(parents=True)
    (d / "bundle.json").write_text("{}", encoding="utf-8")
    return d


def test_move_to_trash_moves_under_dot_trash(tmp_path):
    d = _mkrun(tmp_path, "run_a")
    dest = trash.move_to_trash(d, tmp_path)
    assert dest == tmp_path / ".trash" / "run_a"
    assert dest.exists() and (dest / "bundle.json").exists()
    assert not d.exists()  # original gone


def test_move_to_trash_collision_safe(tmp_path):
    d1 = _mkrun(tmp_path, "dup")
    trash.move_to_trash(d1, tmp_path)
    d2 = _mkrun(tmp_path, "dup")  # same name trashed again
    dest = trash.move_to_trash(d2, tmp_path)
    assert dest == tmp_path / ".trash" / "dup__2"
    assert dest.exists()


def test_list_trash_lists_dirs_sorted(tmp_path):
    assert trash.list_trash(tmp_path) == []  # no .trash yet
    for n in ("z_run", "a_run"):
        trash.move_to_trash(_mkrun(tmp_path, n), tmp_path)
    assert trash.list_trash(tmp_path) == ["a_run", "z_run"]


def test_restore_from_trash_moves_back(tmp_path):
    trash.move_to_trash(_mkrun(tmp_path, "run_b"), tmp_path)
    dest = trash.restore_from_trash("run_b", tmp_path)
    assert dest == tmp_path / "run_b"
    assert dest.exists() and (dest / "bundle.json").exists()
    assert not (tmp_path / ".trash" / "run_b").exists()
    assert trash.list_trash(tmp_path) == []


def test_restore_collision_safe(tmp_path):
    # a run with the same name already exists in runs/ → restore must not clobber it
    trash.move_to_trash(_mkrun(tmp_path, "run_c"), tmp_path)
    _mkrun(tmp_path, "run_c")  # a new live run took the name
    dest = trash.restore_from_trash("run_c", tmp_path)
    assert dest == tmp_path / "run_c__2"
    assert dest.exists()


def test_restore_rejects_traversal(tmp_path):
    (tmp_path / ".trash").mkdir()
    with pytest.raises(ValueError):
        trash.restore_from_trash("../escape", tmp_path)


def test_move_to_trash_rejects_dir_outside_runs(tmp_path):
    outside = tmp_path.parent / "outside_run"
    outside.mkdir()
    with pytest.raises(ValueError):
        trash.move_to_trash(outside, tmp_path)
