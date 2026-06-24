# tests/test_gui_control_sentinel.py
import os
import subprocess
import sys
import time

import psutil

from touchstone.gui import control


def test_write_read_roundtrip(tmp_path):
    rd = tmp_path / "run1"
    rd.mkdir()
    control.write_sentinel(
        rd,
        kind="eval",
        pid=os.getpid(),
        pack_path="packs/ndassist.yaml",
        config_path="config.m5.yaml",
    )
    s = control.read_sentinel(rd)
    assert s is not None
    assert s["kind"] == "eval" and s["pid"] == os.getpid()
    assert s["state"] == "running"


def test_is_active_true_for_live_pid(tmp_path):
    rd = tmp_path / "run2"
    rd.mkdir()
    control.write_sentinel(rd, kind="eval", pid=os.getpid(), pack_path="p", config_path="c")
    assert control.is_active(control.read_sentinel(rd)) is True


def test_is_active_false_for_dead_pid(tmp_path):
    rd = tmp_path / "run3"
    rd.mkdir()
    control.write_sentinel(
        rd,
        kind="eval",
        pid=2_000_000_000,  # almost certainly dead
        pack_path="p",
        config_path="c",
    )
    assert control.is_active(control.read_sentinel(rd)) is False


def test_is_active_false_for_zombie_child(tmp_path):
    """A finished-but-unreaped child (zombie) must read as not-active.

    The GUI server spawns the eval/judge CLI via Popen and never wait()s it, so a
    finished run lingers as a zombie. os.kill(zombie, 0) succeeds, which kept the
    overview card / SSE stream stuck on 'running' forever (the reported bug).
    """
    proc = subprocess.Popen([sys.executable, "-c", ""])
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            if psutil.Process(proc.pid).status() == psutil.STATUS_ZOMBIE:
                break
            time.sleep(0.01)
        rd = tmp_path / "zrun"
        rd.mkdir()
        control.write_sentinel(rd, kind="judge", pid=proc.pid, pack_path="p", config_path="c")
        assert control.is_active(control.read_sentinel(rd)) is False
    finally:
        proc.wait()  # reap the zombie


def test_finalize_sentinel_marks_finished(tmp_path):
    rd = tmp_path / "runf"
    rd.mkdir()
    control.write_sentinel(rd, kind="eval", pid=os.getpid(), pack_path="p", config_path="c")
    control.finalize_sentinel(rd, ok=True)
    assert control.read_sentinel(rd)["state"] == "finished"


def test_finalize_sentinel_marks_failed(tmp_path):
    rd = tmp_path / "runx"
    rd.mkdir()
    control.write_sentinel(rd, kind="judge", pid=os.getpid(), pack_path="p", config_path="c")
    control.finalize_sentinel(rd, ok=False)
    assert control.read_sentinel(rd)["state"] == "failed"


def test_finalize_sentinel_noop_without_sentinel(tmp_path):
    # A plain CLI run (no GUI) has no run.json — finalize must never create one.
    rd = tmp_path / "bare"
    rd.mkdir()
    control.finalize_sentinel(rd, ok=True)
    assert control.read_sentinel(rd) is None


def test_finalize_sentinel_keeps_stopped(tmp_path):
    # User pressed stop → state 'stopped'; a late finalize must not clobber it.
    rd = tmp_path / "runs"
    rd.mkdir()
    control.write_sentinel(rd, kind="eval", pid=os.getpid(), pack_path="p", config_path="c")
    control.mark_sentinel(rd, "stopped")
    control.finalize_sentinel(rd, ok=True)
    assert control.read_sentinel(rd)["state"] == "stopped"


def test_read_missing_returns_none(tmp_path):
    assert control.read_sentinel(tmp_path / "nope") is None


def test_mark_and_clear(tmp_path):
    rd = tmp_path / "run4"
    rd.mkdir()
    control.write_sentinel(rd, kind="judge", pid=os.getpid(), pack_path="p", config_path="c")
    control.mark_sentinel(rd, "finished")
    assert control.read_sentinel(rd)["state"] == "finished"
    control.clear_sentinel(rd)
    assert control.read_sentinel(rd) is None
