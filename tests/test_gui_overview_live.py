from __future__ import annotations

import json
import os

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


def _running_bundle(tmp_path):
    d = tmp_path / "2026_eval_running"
    d.mkdir()
    d.joinpath("bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-22",
            }
        ),
        encoding="utf-8",
    )
    # a "running" sentinel whose pid is this (alive) test process → classify() => "running"
    d.joinpath("run.json").write_text(
        json.dumps(
            {
                "kind": "eval",
                "run_dir": str(d),
                "pid": os.getpid(),
                "pack_path": "packs/ndassist.yaml",
                "config_path": "config.example.yaml",
                "started_ts": 0.0,
                "state": "running",
            }
        ),
        encoding="utf-8",
    )
    return d


def test_overview_running_card_uses_native_eventsource(tmp_path):
    _running_bundle(tmp_path)
    body = _client(tmp_path).get("/").text
    assert "liveProgress(" in body  # native-EventSource Alpine component drives the progress
    assert "/static/live_progress.js" in body
    # the broken htmx-SSE wiring (extension was never bundled) must be gone
    assert "sse-connect" not in body
    assert 'hx-ext="sse"' not in body
