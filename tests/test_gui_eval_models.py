# tests/test_gui_eval_models.py
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from touchstone.cli import app as cli_app
from touchstone.config import ModelSpec
from touchstone.gui import app as gui_app
from touchstone.gui import control


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


def test_route_redirects_to_overview_not_json(tmp_path):
    # post-redirect-get: a plain form POST must land back in the GUI, not on a raw JSON dict
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={
            "pack_path": "packs/ndassist.yaml",
            "config_path": "config.m5.yaml",
            "models_json": '[{"id":"a"}]',
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "--models-json" in rec.calls[0]  # still spawned


def test_judge_route_redirects_to_overview(tmp_path):
    (tmp_path / "b1").mkdir()
    client, _ = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/judge",
        data={"bundle": "b1", "judge_config_path": "judge.yaml"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"


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
    # Must load NON-deferred so it registers modelPicker before the deferred Alpine starts and
    # fires alpine:init; otherwise the picker is dead (the select/:disabled never bind).
    assert '<script src="/static/model_picker.js">' in body
    assert 'defer src="/static/model_picker.js"' not in body
    # single-select picker: one <select> bound to `chosen`, a manual escape hatch, submit guard
    assert 'x-model="chosen"' in body
    assert "__manual__" in body  # the "andere Modell-ID" escape-hatch option
    assert ":disabled" in body  # submit disabled at 0 models (no empty-submit dead-end)
    # the old multi-select machinery is gone (one model per run)
    assert "+ Modell" not in body
    assert "Vom Endpoint" not in body


def test_config_page_hides_picker_on_resume(tmp_path):
    client, _ = _client_and_launcher(tmp_path)
    body = client.get("/config?resume=somebundle").text
    assert "modelPicker(" not in body
    assert 'name="models_json"' not in body


# ── Review fixes ──────────────────────────────────────────────────────────────


def test_route_blank_id_is_400_and_no_spawn(tmp_path):
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={"pack_path": "p", "config_path": "c", "models_json": '[{"id":""}]'},
    )
    assert r.status_code == 400
    assert rec.calls == []


def test_route_resume_ignores_models_json(tmp_path):
    (tmp_path / "2026_eval_x").mkdir()  # resume dir must exist under runs_dir for _confine
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={
            "pack_path": "p",
            "config_path": "c",
            "resume_dir": "2026_eval_x",
            "models_json": '[{"id":"a"}]',
        },
    )
    assert r.status_code == 200
    argv = rec.calls[0]
    assert "--resume" in argv
    assert "--models-json" not in argv  # override ignored on resume (M7)


def test_route_resume_ignores_even_invalid_models_json(tmp_path):
    (tmp_path / "2026_eval_y").mkdir()
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={
            "pack_path": "p",
            "config_path": "c",
            "resume_dir": "2026_eval_y",
            "models_json": "{bad",
        },
    )
    assert r.status_code == 200  # not 400 — override path skipped on resume
    assert "--models-json" not in rec.calls[0]


def test_eval_cmd_applies_override_and_skips_on_resume(tmp_path, monkeypatch):
    import touchstone.cli as cli

    captured: dict[str, list[str]] = {}

    def fake_run_eval(cfg, pk, client, **kw):
        captured["models"] = [m.id for m in cfg.models]
        return []

    monkeypatch.setattr(cli, "run_eval", fake_run_eval)
    monkeypatch.setattr(cli, "_make_client", lambda cfg: None)
    monkeypatch.setattr(cli, "_finalize_eval_bundle", lambda *a, **k: None)
    runner = CliRunner()

    # non-resume: --models-json replaces config.models for the run
    res = runner.invoke(
        cli.app,
        [
            "eval",
            "--pack",
            "packs/ndassist.yaml",
            "--config",
            "config.m5.yaml",
            "--models-json",
            '[{"id":"OVERRIDE"}]',
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["models"] == ["OVERRIDE"]

    # resume: override is skipped (bundle cells are fixed)
    captured.clear()
    rdir = tmp_path / "rz"
    rdir.mkdir()
    res2 = runner.invoke(
        cli.app,
        [
            "eval",
            "--pack",
            "packs/ndassist.yaml",
            "--config",
            "config.m5.yaml",
            "--resume",
            str(rdir),
            "--models-json",
            '[{"id":"OVERRIDE"}]',
        ],
    )
    assert res2.exit_code == 0, res2.output
    assert "OVERRIDE" not in captured["models"]
