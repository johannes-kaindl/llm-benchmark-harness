from __future__ import annotations

import re

from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui import configs as configs_mod
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


def test_config_default_is_not_embed(tmp_path):
    # the config <select> options follow order_configs (embed/vlm last) and the picker defaults
    # to configs[0]; assert the first config option is a non-embed/non-vlm config. (Decoupled
    # from JSON key order — no longer relies on a global tojson sort_keys policy.)
    body = _client(tmp_path).get("/config").text
    block = re.search(r'id="config_path".*?</select>', body, re.S)
    assert block is not None
    first = re.search(r'<option value="(config\.[^"]+\.yaml)"', block.group(0))
    assert first is not None
    assert "embed" not in first.group(1) and "vlm" not in first.group(1)


def test_config_page_has_single_model_select(tmp_path):
    body = _client(tmp_path).get("/config").text
    assert 'x-model="chosen"' in body  # the single-select bound to component state
    assert "fetchModelOptions" not in body  # JS lives in model_picker.js, not inline
    assert "endpointLoading" in body  # status state surfaced (spinner/text)
    # the three-way add machinery is gone — one obvious path
    assert "endpointPick" not in body
    assert "Vom Endpoint" not in body
    assert "Hinzufügen" not in body


def test_config_page_model_select_hidden_on_resume(tmp_path):
    body = _client(tmp_path).get("/config?resume=foo").text
    assert 'x-model="chosen"' not in body
    assert "modelPicker(" not in body


def test_config_page_model_select_tracks_user_interaction(tmp_path):
    # the model <select> must flag user interaction so a late (≤3s) endpoint fetch cannot
    # clobber a model the user already picked (default-adoption race).
    body = _client(tmp_path).get("/config").text
    assert "userChose" in body  # @change wires the interaction guard on the model select


def test_eval_model_options_route_merges_endpoint_and_config(tmp_path, monkeypatch):
    # config.m5.yaml declares qwen3.6-35b-a3b-4bit; endpoint serves "x" → merged single-select
    monkeypatch.setattr(
        configs_mod, "discover_endpoint_models", lambda config: {"models": ["x"], "error": None}
    )
    r = _client(tmp_path).get("/eval-model-options?config=config.m5.yaml")
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert body["default_id"] == "x"  # served model is the default, not the config placeholder
    ids = [(o["id"], o["served"]) for o in body["options"]]
    assert ("x", True) in ids
    assert ("qwen3.6-35b-a3b-4bit", False) in ids  # declared-but-not-served, flagged


def test_eval_model_options_route_error_is_200(tmp_path, monkeypatch):
    monkeypatch.setattr(
        configs_mod, "discover_endpoint_models", lambda config: {"models": [], "error": "down"}
    )
    r = _client(tmp_path).get("/eval-model-options?config=config.m5.yaml")
    assert r.status_code == 200  # offline endpoint is not a server error
    assert r.json()["error"] == "down"
    # offline → config models are the fallback options (default to the first one)
    assert r.json()["default_id"] == "qwen3.6-35b-a3b-4bit"


def test_eval_model_options_route_rejects_traversal(tmp_path):
    assert _client(tmp_path).get("/eval-model-options?config=../etc/passwd").status_code == 404


def test_eval_model_options_route_rejects_non_config_yaml(tmp_path):
    assert (
        _client(tmp_path).get("/eval-model-options?config=packs/ndassist.yaml").status_code == 404
    )


def test_judge_endpoint_models_guards_path_and_never_500(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "judge.yaml").write_text(
        "endpoint:\n  base_url: http://x/v1\nmodel: qwen\n", encoding="utf-8"
    )
    client = _client(tmp_path)

    # unknown / traversal path → 404
    assert client.get("/judge-endpoint-models?judge_config=/etc/passwd").status_code == 404
    # known judge config → 200, never 500 even if endpoint is dead
    r = client.get("/judge-endpoint-models?judge_config=judge.yaml")
    assert r.status_code == 200
    assert "models" in r.json() and "error" in r.json()
