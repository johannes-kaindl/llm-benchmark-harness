import pytest

from ramcheck.gui import control


class FakeLauncher:
    """Records argv, simulates a process whose liveness we toggle."""

    def __init__(self):
        self.calls = []
        self._alive = True
        self.pid = 4242
        self.terminated = False

    def spawn(self, argv):
        self.calls.append(argv)
        return self.pid

    def alive(self, pid):
        return self._alive

    def terminate(self, pid):
        self.terminated = True
        self._alive = False


def _reg(tmp_path):
    return control.RunRegistry(runs_dir=tmp_path, launcher=FakeLauncher())


def test_start_eval_writes_sentinel_and_passes_run_dir(tmp_path):
    reg = _reg(tmp_path)
    handle = reg.start_eval(pack_path="packs/ndassist.yaml", config_path="config.m5.yaml")
    argv = reg.launcher.calls[0]
    assert "--run-dir" in argv and str(handle.run_dir) in argv
    assert "--emit-events" in argv and "eval" in argv
    assert control.read_sentinel(handle.run_dir)["kind"] == "eval"


def test_second_start_blocked_by_active_sentinel(tmp_path):
    reg = _reg(tmp_path)
    reg.start_eval(pack_path="p", config_path="c")
    with pytest.raises(control.RunInProgress):
        reg.start_eval(pack_path="p2", config_path="c2")


def test_lock_survives_fresh_registry(tmp_path):
    """A new registry (simulated GUI restart) still sees the on-disk sentinel lock."""
    reg1 = _reg(tmp_path)
    reg1.start_eval(pack_path="p", config_path="c")
    reg2 = control.RunRegistry(runs_dir=tmp_path, launcher=FakeLauncher())
    with pytest.raises(control.RunInProgress):
        reg2.start_eval(pack_path="p", config_path="c")


def test_stale_sentinel_is_reclaimed(tmp_path):
    reg = _reg(tmp_path)
    h = reg.start_eval(pack_path="p", config_path="c")
    reg.launcher._alive = False  # process died without cleanup
    # a new start should now succeed (stale lock reclaimed)
    h2 = reg.start_eval(pack_path="p2", config_path="c2")
    assert h2.run_dir != h.run_dir


def test_poll_maps_exit(tmp_path):
    reg = _reg(tmp_path)
    h = reg.start_eval(pack_path="p", config_path="c")
    assert reg.poll(h) == "running"
    reg.launcher._alive = False
    assert reg.poll(h) in {"finished", "failed"}


def test_stop_terminates_and_marks(tmp_path):
    reg = _reg(tmp_path)
    h = reg.start_eval(pack_path="p", config_path="c")
    reg.stop(h)
    assert reg.launcher.terminated
    assert control.read_sentinel(h.run_dir)["state"] == "stopped"
