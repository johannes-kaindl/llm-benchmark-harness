# tests/test_gui_control_sentinel.py
import os

from ramcheck.gui import control


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
