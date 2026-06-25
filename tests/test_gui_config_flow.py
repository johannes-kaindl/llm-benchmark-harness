# tests/test_gui_config_flow.py
from fastapi.testclient import TestClient

from touchstone.gui import app as appmod
from touchstone.gui.control import RunRegistry


class _FakeLauncher:
    def spawn(self, argv): return 1
    def alive(self, pid): return False
    def terminate(self, pid): return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(appmod.create_app(runs_dir=tmp_path, registry=reg))


def test_config_renders_flow_stations(tmp_path):
    body = _client(tmp_path).get("/config").text
    # the flow markers (Eval → Judge → Ergebnis), no 1/2/3 station numbers
    assert "Eval starten" in body
    assert "Judge starten" in body
    assert "Ergebnis" in body
    # an Alpine scope holding the active station
    assert "x-data" in body and "station" in body
    # result step links to the overview
    assert 'href="/"' in body


def test_config_preserves_eval_and_judge_function_markers(tmp_path):
    body = _client(tmp_path).get("/config").text
    # model picker + judge picker + their hidden/submit machinery survive the reorder
    assert "modelPicker(" in body
    assert "judgeModelPicker(" in body
    assert 'name="models_json"' in body
    assert 'action="/runs/eval"' in body and 'action="/runs/judge"' in body
