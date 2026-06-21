# tests/test_gui_eval_models.py
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ramcheck.cli import app as cli_app
from ramcheck.config import ModelSpec
from ramcheck.gui import app as gui_app
from ramcheck.gui import control


class _Rec:
    def __init__(self):
        self.calls = []

    def spawn(self, argv):
        self.calls.append(argv)
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def test_start_eval_adds_models_json_when_given(tmp_path):
    rec = _Rec()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=rec)
    reg.start_eval(
        pack_path="packs/ndassist.yaml",
        config_path="config.m5.yaml",
        models=[ModelSpec(id="a", quant="Q4"), ModelSpec(id="b")],
    )
    argv = rec.calls[0]
    assert "--models-json" in argv
    payload = json.loads(argv[argv.index("--models-json") + 1])
    assert [m["id"] for m in payload] == ["a", "b"]
    assert payload[0]["quant"] == "Q4"


def test_start_eval_omits_models_json_when_none(tmp_path):
    rec = _Rec()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=rec)
    reg.start_eval(pack_path="p", config_path="c")
    assert "--models-json" not in rec.calls[0]


def test_eval_cmd_exposes_models_json_option():
    res = CliRunner().invoke(cli_app, ["eval", "--help"])
    assert res.exit_code == 0
    assert "--models-json" in res.output


def _client_and_launcher(tmp_path):
    rec = _Rec()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=rec)
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg)), rec


def test_route_valid_models_json_spawns_with_flag(tmp_path):
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={
            "pack_path": "packs/ndassist.yaml",
            "config_path": "config.m5.yaml",
            "models_json": '[{"id":"a","quant":"Q4"}]',
        },
    )
    assert r.status_code == 200
    assert "--models-json" in rec.calls[0]


def test_route_empty_array_is_400_and_no_spawn(tmp_path):
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={"pack_path": "p", "config_path": "c", "models_json": "[]"},
    )
    assert r.status_code == 400
    assert rec.calls == []


def test_route_invalid_models_json_is_400_and_no_spawn(tmp_path):
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={"pack_path": "p", "config_path": "c", "models_json": "{bad"},
    )
    assert r.status_code == 400
    assert rec.calls == []


def test_route_no_models_json_still_works(tmp_path):
    client, rec = _client_and_launcher(tmp_path)
    r = client.post("/runs/eval", data={"pack_path": "p", "config_path": "c"})
    assert r.status_code == 200
    assert "--models-json" not in rec.calls[0]


def test_config_page_renders_model_picker(tmp_path):
    client, _ = _client_and_launcher(tmp_path)
    body = client.get("/config").text
    assert "modelPicker(" in body  # Alpine component bound
    assert 'name="models_json"' in body  # hidden field present
    assert "/static/model_picker.js" in body
    assert "+ Modell" in body  # ad-hoc add button


def test_config_page_hides_picker_on_resume(tmp_path):
    client, _ = _client_and_launcher(tmp_path)
    body = client.get("/config?resume=somebundle").text
    assert "modelPicker(" not in body
    assert 'name="models_json"' not in body
