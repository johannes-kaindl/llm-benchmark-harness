"""`tools` must not report success while cells were never measured (CORE-TEST-19).

A transport error is not a model result: the cell is *open* and `--resume` re-runs it. Fewer
than `max_consecutive_errors` such cells in a row do not abort the run — so before this test the
command finished with rc=0 and a line reading "N/M Items bestanden", where the open cells were
silently folded into the "not passed" side. A driver script (the overnight queue) could not tell
"the model failed the check" from "we never got an answer".

The stand-in kills the connection mid-stream for selected items; everything else streams a
normal, answer-free turn.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from touchstone.cli import app

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def server() -> Iterator[tuple[str, set[int]]]:
    """(base_url, kill) — add a matrix-request ordinal to `kill` and that request dies.

    Ordinals count chat requests AFTER the pre-flight one (so 1 = the first matrix cell). The
    stand-in never emits a tool call, so the agent loop sends exactly one request per cell and
    the ordinal identifies the cell; keying on the item id would not work, as the id never
    appears in the prompt.
    """
    kill: set[int] = set()
    n = {"posts": 0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # /api/v0/models probe → nothing loaded
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data": []}')

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            n["posts"] += 1
            ordinal = n["posts"] - 1  # the pre-flight request is 0
            if ordinal in kill:
                # RST, not a graceful close: a clean EOF would look like a finished stream.
                self.connection.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
                self.connection.close()
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m"}
            for delta, fin in (({"role": "assistant", "content": "..."}, None), ({}, "stop")):
                c = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *a: Any) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/v1", kill
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


def _run(tmp_path: Path, url: str, items: str, run: Path) -> Any:
    return CliRunner().invoke(
        app,
        ["tools", "--pack", str(REPO / "packs/opencode-tools.yaml"), "-c",
         str(_config(tmp_path, url)), "--run-dir", str(run), "--items", items,
         "--models-json", json.dumps([{"id": "m", "quant": "q"}])],
    )  # fmt: skip


def test_open_error_cell_fails_the_command(server: tuple[str, set[int]], tmp_path: Path) -> None:
    """One dropped connection — too few to abort the run — must still fail the command."""
    url, kill = server
    kill.add(1)
    res = _run(tmp_path, url, "S6", tmp_path / "run")
    assert res.exit_code != 0, res.output
    out = " ".join(res.output.split())
    assert "1 offene Fehler-Zelle" in out, out
    assert "--resume" in out, out


def test_scattered_error_cell_fails_although_other_items_were_measured(
    server: tuple[str, set[int]], tmp_path: Path
) -> None:
    """The mixed case is the dangerous one: some items really were measured, so the run looks
    healthy. rc must still be non-zero, and the count must name only the open cell."""
    url, kill = server
    kill.add(2)  # the second matrix cell (S2)
    run = tmp_path / "run"
    res = _run(tmp_path, url, "S1,S2,S3", run)
    assert res.exit_code != 0, res.output
    out = " ".join(res.output.split())
    assert "1 offene Fehler-Zelle" in out, out
    rows = [json.loads(ln) for ln in (run / "responses.jsonl").read_text("utf-8").splitlines()]
    assert {r["item_id"] for r in rows} == {"S1", "S2", "S3"}
    assert [r["item_id"] for r in rows if r["error"]] == ["S2"]


def test_clean_run_still_succeeds(server: tuple[str, set[int]], tmp_path: Path) -> None:
    """Counter-probe: without a dropped connection the very same path must return rc=0 — the
    guard must key on open cells, not on 'some item did not pass its checks' (none do here:
    the stand-in never emits a tool call, so every item legitimately fails its checks)."""
    url, _kill = server
    run = tmp_path / "run"
    res = _run(tmp_path, url, "S1,S2,S3", run)
    assert res.exit_code == 0, res.output
    out = " ".join(res.output.split())
    assert "offene Fehler-Zelle" not in out, out
    rows = [json.loads(ln) for ln in (run / "responses.jsonl").read_text("utf-8").splitlines()]
    assert len(rows) == 3 and not any(r["error"] for r in rows)
    assert not any(r["passed"] for r in rows)  # measured and failed — that is NOT an error cell
