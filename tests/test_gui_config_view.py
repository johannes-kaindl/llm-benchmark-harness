# tests/test_gui_config_view.py
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry


class _FakeLauncher:
    def spawn(self, argv):
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def test_config_view_renders_key_fields(tmp_path):
    r = _client(tmp_path).get("/config-view/config.m5.yaml")
    assert r.status_code == 200
    # Endpoint base_url, machine, runs_per_cell and a model id must surface.
    assert "http://localhost:8080/v1" in r.text
    assert "M5-32GB" in r.text
    assert "runs_per_cell" in r.text or "Runs pro Zelle" in r.text
    assert "qwen3.6-35b-a3b-4bit" in r.text


def test_config_view_rejects_traversal(tmp_path):
    # Encode the slashes so httpx does NOT normalize the dot-segments away before sending —
    # this actually reaches the handler's in-route '..'/glob guard (a bare '../etc/passwd'
    # would be normalized by the client and 404 as a mere route-miss, never exercising it).
    r = _client(tmp_path).get("/config-view/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code == 404
