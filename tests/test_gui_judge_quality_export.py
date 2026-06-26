import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry


class _L:
    def spawn(self, a):
        return 1

    def alive(self, p):
        return False

    def terminate(self, p):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_L())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def test_judge_quality_is_single_file_exportable(tmp_path):
    d = tmp_path / "2026_eval_nd"
    d.mkdir()
    (d / "judge_quality.md").write_text("# Judge-Qualität\n", encoding="utf-8")
    r = _client(tmp_path).get(f"/export/{d.name}/judge_quality.md")
    assert r.status_code == 200
    assert "Judge-Qualität" in r.text
