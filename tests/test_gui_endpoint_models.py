from __future__ import annotations

import re

from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui import configs as configs_mod
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


def test_endpoint_models_route_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        configs_mod,
        "discover_endpoint_models",
        lambda config: {"models": ["x", "y"], "error": None},
    )
    r = _client(tmp_path).get("/endpoint-models?config=config.m5.yaml")
    assert r.status_code == 200
    assert r.json() == {"models": ["x", "y"], "error": None}


def test_endpoint_models_route_error_is_200(tmp_path, monkeypatch):
    monkeypatch.setattr(
        configs_mod, "discover_endpoint_models", lambda config: {"models": [], "error": "down"}
    )
    r = _client(tmp_path).get("/endpoint-models?config=config.m5.yaml")
    assert r.status_code == 200  # offline endpoint is not a server error
    assert r.json()["error"] == "down"


def test_endpoint_models_route_rejects_traversal(tmp_path):
    assert _client(tmp_path).get("/endpoint-models?config=../etc/passwd").status_code == 404


def test_endpoint_models_route_rejects_non_config_yaml(tmp_path):
    # only the offered config*.yaml are readable — no arbitrary cwd YAML (info-disclosure guard)
    assert _client(tmp_path).get("/endpoint-models?config=packs/ndassist.yaml").status_code == 404


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


def test_config_page_has_endpoint_dropdown(tmp_path):
    body = _client(tmp_path).get("/config").text
    assert "endpointPick" in body  # dropdown bound to component state
    assert "fetchEndpointModels" not in body  # JS lives in model_picker.js, not inline
    assert "Vom Endpoint" in body  # section label
    assert "Hinzufügen" in body  # add button


def test_config_page_endpoint_dropdown_hidden_on_resume(tmp_path):
    body = _client(tmp_path).get("/config?resume=foo").text
    assert "endpointPick" not in body
