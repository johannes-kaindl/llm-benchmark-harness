# tests/test_gui_app_control.py
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunInProgress, RunRegistry


class _FakeReg(RunRegistry):
    def __init__(self):
        self.started = []
        self.stopped = []

    def start_eval(self, *, pack_path, config_path, resume_dir=None):
        from pathlib import Path

        from ramcheck.gui.control import RunHandle

        self.started.append(("eval", pack_path, config_path))
        return RunHandle("eval", Path("runs/x"), 1)

    def stop(self, handle):
        self.stopped.append(handle)


def _client(tmp_path, reg):
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def test_start_eval_calls_registry(tmp_path):
    reg = _FakeReg()
    r = _client(tmp_path, reg).post(
        "/runs/eval", data={"pack_path": "packs/ndassist.yaml", "config_path": "config.m5.yaml"}
    )
    assert r.status_code in (200, 303)
    assert reg.started and reg.started[0][1] == "packs/ndassist.yaml"


def test_start_blocked_returns_conflict(tmp_path):
    class _Busy(_FakeReg):
        def start_eval(self, **k):
            raise RunInProgress("busy")

    r = _client(tmp_path, _Busy()).post("/runs/eval", data={"pack_path": "p", "config_path": "c"})
    assert r.status_code == 409
