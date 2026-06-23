# tests/test_gui_pack_view.py
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunRegistry


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


def test_pack_view_shows_full_prompt_text(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    assert r.status_code == 200
    # A substring that lives >80 chars into the A1 prompt body must appear in full,
    # i.e. the prompt is rendered untruncated (no longer just the title).
    assert "Wenn ich nur dran denke, mache ich dicht und scrolle stattdessen am Handy" in r.text


def test_pack_view_shows_full_system_prompt_untruncated(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    assert r.status_code == 200
    # The old template sliced the system prompt at [:80] and appended "…".
    # "Validiere" sits at index 133 of the baseline system prompt, so seeing it
    # proves the full text is rendered.
    assert "Validiere zuerst, gib dann konkrete, kleinschrittige Hilfe" in r.text


def test_pack_view_shows_rubric_tests(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    assert r.status_code == 200
    # The per-prompt rubric (PackPrompt.tests) must be surfaced alongside the prompt.
    assert "Zerlegung in winzige Schritte, Umgang mit Aufschiebe-Lähmung" in r.text
