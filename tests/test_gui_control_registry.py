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


def test_poll_running_then_failed_when_never_finalized(tmp_path):
    """A dead pid with no bundle.json/responses.jsonl is a crash → 'failed', not 'finished'."""
    reg = _reg(tmp_path)
    h = reg.start_eval(pack_path="p", config_path="c")
    assert reg.poll(h) == "running"
    reg.launcher._alive = False  # kill -9: process gone, nothing finalized
    assert reg.poll(h) == "failed"
    assert control.read_sentinel(h.run_dir)["state"] == "failed"


def test_poll_finished_when_finalized(tmp_path):
    """A dead pid with a finalized run (responses.jsonl present) → 'finished'."""
    reg = _reg(tmp_path)
    h = reg.start_eval(pack_path="p", config_path="c")
    (h.run_dir / "responses.jsonl").write_text("", encoding="utf-8")
    reg.launcher._alive = False
    assert reg.poll(h) == "finished"
    assert control.read_sentinel(h.run_dir)["state"] == "finished"


def test_stop_terminates_and_marks(tmp_path):
    reg = _reg(tmp_path)
    h = reg.start_eval(pack_path="p", config_path="c")
    reg.stop(h)
    assert reg.launcher.terminated
    assert control.read_sentinel(h.run_dir)["state"] == "stopped"


def test_sentinel_written_before_spawn(tmp_path):
    """The on-disk lock must exist BEFORE spawn so no guard-window opens (MAJOR 1)."""

    class _RecordingLauncher:
        def __init__(self):
            self.spawns = []
            self.sentinel_at_spawn = None
            self.runs_dir = tmp_path

        def spawn(self, argv):
            self.spawns.append(argv)
            # At spawn time, the sentinel (the only lock) must already exist.
            run_dir = tmp_path / argv[argv.index("--run-dir") + 1]
            self.sentinel_at_spawn = control.read_sentinel(run_dir) is not None
            return 4242

        def alive(self, pid):
            return False

        def terminate(self, pid):
            pass

    launcher = _RecordingLauncher()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=launcher)
    reg.start_eval(pack_path="p", config_path="c")
    assert launcher.sentinel_at_spawn is True


def test_concurrent_starts_only_one_spawn(tmp_path):
    """Two concurrent starts: the lock must serialize guard+sentinel+spawn so the
    second raises RunInProgress even before the first's launcher 'alive'-flips —
    i.e. the second must enter start_eval while the first is mid-spawn (TOCTOU, MAJOR 1)."""
    import threading

    in_spawn = threading.Event()
    second_arrived = threading.Event()

    class _SlowLauncher:
        def __init__(self):
            self.spawns = []
            self._alive_pids: set[int] = set()
            self._first = True

        def spawn(self, argv):
            self.spawns.append(argv)
            pid = len(self.spawns)
            if self._first:
                self._first = False
                # Signal we are mid-spawn, then wait for the second start to actually
                # arrive at start_eval (proving it would overlap without the lock).
                in_spawn.set()
                second_arrived.wait(timeout=5)
            self._alive_pids.add(pid)  # mirror a real running subprocess
            return pid

        def alive(self, pid):
            return pid in self._alive_pids

        def terminate(self, pid):
            self._alive_pids.discard(pid)

    launcher = _SlowLauncher()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=launcher)
    errors: list[Exception] = []
    blocked = []

    def first():
        try:
            reg.start_eval(pack_path="p1", config_path="c1")
        except Exception as e:  # pragma: no cover - first should succeed
            errors.append(e)

    def second():
        in_spawn.wait(timeout=5)  # only start after the first is mid-spawn
        second_arrived.set()  # let the first finish spawning
        try:
            reg.start_eval(pack_path="p2", config_path="c2")
        except control.RunInProgress:
            blocked.append(True)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not errors, errors
    # Only the first start may have spawned — the second must have been blocked.
    assert len(launcher.spawns) == 1
    assert blocked == [True]
