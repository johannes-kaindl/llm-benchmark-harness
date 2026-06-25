# tests/test_gui_config_flow.py
from fastapi.testclient import TestClient

from touchstone.gui import app as appmod
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


def test_flow_bar_class_and_css_rule_present(tmp_path):
    """flow-bar carries the right class in HTML and the CSS rule exists."""
    from pathlib import Path

    body = _client(tmp_path).get("/config").text
    assert 'class="flow-bar"' in body, "flow-bar wrapper must carry .flow-bar class"
    css_path = Path(__file__).parent.parent / "touchstone" / "gui" / "static" / "app.css"
    css = css_path.read_text()
    assert ".flow-bar" in css, "app.css must contain a .flow-bar rule"
    assert ".flow-bar button.active" in css, "app.css must contain .flow-bar button.active rule"


def test_config_sidesteps_import_always_resume_conditional(tmp_path):
    # no resume → import present, resume hint absent
    body = _client(tmp_path).get("/config").text
    assert "Bundle importieren" in body
    assert 'action="/import-bundle"' in body
    assert "Fortsetzen" not in body  # resume hint only in resume mode
    # resume mode → resume hint present
    body2 = _client(tmp_path).get("/config?resume=2026-01-01_run").text
    assert "Fortsetzen" in body2
    assert "2026-01-01_run" in body2
