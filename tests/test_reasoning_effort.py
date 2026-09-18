"""reasoning_effort end-to-end through the real CLI against a local OpenAI-compatible stand-in.

LM Studio takes ``reasoning_effort`` from the TOP LEVEL of the request body (not from
``chat_template_kwargs``) and the qwen template raises on unknown values. The stand-in mimics
that: it records every body and answers 400 with a template error for an invalid effort. This
pins what we SEND and that a rejection fails loudly; LM Studio's real behaviour is a smoke item.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from touchstone.cli import app

VALID = {"xhigh", "medium", "low"}
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def server() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    bodies: list[dict[str, Any]] = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # /api/v0/models probe → nothing loaded
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data": []}')

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            bodies.append(body)
            effort = body.get("reasoning_effort", "xhigh")
            if effort not in VALID:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                msg = f"Error rendering prompt: Unexpected reasoning effort {effort!r}"
                self.wfile.write(json.dumps({"error": {"message": msg}}).encode())
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m"}
            for delta, fin in (({"role": "assistant", "content": "OK"}, None), ({}, "stop")):
                c = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *a: Any) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/v1", bodies
    finally:
        srv.shutdown()


def _config(tmp_path: Path, base_url: str) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f'endpoint: {{ base_url: "{base_url}", api_key: "x" }}\n'
        'machine: "test"\nmodels:\n  - { id: "placeholder" }\n'
        f'output_dir: "{tmp_path}"\npower_check: false\nserver_process_match: "nothing"\n',
        encoding="utf-8",
    )
    return cfg


def _models(effort: str) -> str:
    return json.dumps(
        [{"id": "qwen/qwen3.8-27b@4bit", "quant": f"4bit-{effort}",
          "extra_body": {"reasoning_effort": effort}}]
    )  # fmt: skip


def test_tools_sends_reasoning_effort_top_level_and_records_it(
    server: tuple[str, list[dict[str, Any]]], tmp_path: Path
) -> None:
    url, bodies = server
    run = tmp_path / "run"
    res = CliRunner().invoke(
        app,
        ["tools", "--pack", str(REPO / "packs/opencode-tools.yaml"), "-c",
         str(_config(tmp_path, url)), "--run-dir", str(run), "--items", "S6",
         "--models-json", _models("medium")],
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    assert len(bodies) == 2  # preflight + S6
    for b in bodies:
        assert b["reasoning_effort"] == "medium"
        assert "chat_template_kwargs" not in b and b["model"] == "qwen/qwen3.8-27b@4bit"
    man = json.loads((run / "bundle.json").read_text(encoding="utf-8"))
    assert man["models"][0]["reasoning_effort"] == "medium"
    assert man["models"][0]["quant"] == "4bit-medium"  # label carries the effort


def test_tools_invalid_effort_fails_loudly_before_the_matrix(
    server: tuple[str, list[dict[str, Any]]], tmp_path: Path
) -> None:
    url, bodies = server
    run = tmp_path / "run"
    res = CliRunner().invoke(
        app,
        ["tools", "--pack", str(REPO / "packs/opencode-tools.yaml"), "-c",
         str(_config(tmp_path, url)), "--run-dir", str(run), "--models-json", _models("hgh")],
    )  # fmt: skip
    assert res.exit_code == 1
    out = " ".join(res.output.split())  # rich wraps long lines
    assert "Pre-Flight" in out and "Unexpected reasoning effort" in out
    assert len(bodies) == 1 and not (run / "responses.jsonl").exists()  # no silent fallback


def test_eval_strict_preflight_fails_on_invalid_effort_and_manifest_keeps_extra_body(
    server: tuple[str, list[dict[str, Any]]], tmp_path: Path
) -> None:
    url, bodies = server
    cfg = _config(tmp_path, url)
    bad = CliRunner().invoke(
        app,
        ["eval", "--pack", str(REPO / "packs/buero.yaml"), "-c", str(cfg), "--run-dir",
         str(tmp_path / "bad"), "--models-json", _models("hgh"), "--strict-preflight"],
    )  # fmt: skip
    assert bad.exit_code != 0
    assert not (tmp_path / "bad" / "responses.jsonl").exists()
    assert bodies and all(b.get("reasoning_effort") == "hgh" for b in bodies)


def test_eval_resume_keeps_the_bundles_model_and_effort(tmp_path: Path) -> None:
    """Resume must continue the bundle's --models-json model (with its extra_body), not the
    config's model list — the overnight driver resumes after memory-contention errors."""
    from touchstone import cli

    run = tmp_path / "b"
    run.mkdir()
    (run / "bundle.json").write_text(
        json.dumps({"models": json.loads(_models("medium"))}), encoding="utf-8"
    )
    seen: dict[str, Any] = {}

    def fake_run_eval(cfg: Any, *a: Any, **kw: Any) -> list[Any]:
        seen["models"] = [(m.id, m.quant, m.extra_body) for m in cfg.models]
        raise SystemExit(0)

    mp = pytest.MonkeyPatch()
    mp.setattr(cli, "run_eval", fake_run_eval)
    try:
        CliRunner().invoke(
            app,
            ["eval", "--pack", str(REPO / "packs/buero.yaml"), "-c",
             str(_config(tmp_path, "http://127.0.0.1:9/v1")), "--resume", str(run)],
        )  # fmt: skip
    finally:
        mp.undo()
    assert seen["models"] == [
        ("qwen/qwen3.8-27b@4bit", "4bit-medium", {"reasoning_effort": "medium"})
    ]
