"""Live monitor server — a separate process that tails events.jsonl + resources.jsonl
and serves an SSE dashboard. Run as: python -m ramcheck.webmon --bundle <dir> --port <p>.

It prints the bound port on stdout (so the parent can open the browser) and binds to
127.0.0.1 only (local, single-user). It never touches the measurement process — it only
reads the two append-only files the run produces.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ramcheck import events as events_mod
from ramcheck import judge_events as judge_events_mod
from ramcheck import loadview as loadview_mod
from ramcheck import tail as tail_mod

POLL_S = 0.25

# Each view module must expose: INDEX_HTML: str, TAILS_RESOURCES: bool,
# parse_line(line: str) -> dict | None, build_view(events) -> obj with .as_dict()
_VIEWS = {"eval": events_mod, "judge": judge_events_mod}


def make_handler(
    bundle: str | Path, events_name: str = "events.jsonl", view: str = "eval"
) -> type[BaseHTTPRequestHandler]:
    bundle_dir = Path(bundle)
    events_path = bundle_dir / events_name
    resources_path = bundle_dir / "resources.jsonl"
    if view not in _VIEWS:
        raise ValueError(f"Unknown view {view!r}. Valid: {sorted(_VIEWS)}")
    view_mod = _VIEWS[view]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # keep stdout clean (parent reads port)
            pass

        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/?"):
                self._serve_index()
            elif self.path.startswith("/events"):
                self._serve_sse()
            else:
                self.send_error(404)

        def _serve_index(self) -> None:
            body = view_mod.INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            all_events: list[dict[str, object]] = []
            offset = 0
            try:
                while True:
                    lines, offset = tail_mod.read_new(events_path, offset)
                    for ln in lines:
                        parsed = view_mod.parse_line(ln)
                        if parsed is not None:
                            all_events.append(parsed)
                    view_obj = view_mod.build_view(all_events)
                    self._send("view", json.dumps(view_obj.as_dict()))
                    if view_mod.TAILS_RESOURCES:
                        load = loadview_mod.latest_load(resources_path)
                        if load is not None:
                            self._send(
                                "load",
                                json.dumps(
                                    {
                                        "sys_used_mb": load.sys_used_mb,
                                        "mem_pressure": load.mem_pressure,
                                        "throttled": load.throttled,
                                        "any_throttle_seen": load.any_throttle_seen,
                                    }
                                ),
                            )
                    time.sleep(POLL_S)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return  # browser closed the connection
            except Exception:
                return  # any other error → end this stream cleanly, never crash the thread

        def _send(self, kind: str, data: str) -> None:
            self.wfile.write(f"event: {kind}\ndata: {data}\n\n".encode())
            self.wfile.flush()

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="ramcheck live monitor server")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--events", default="events.jsonl")
    ap.add_argument("--view", default="eval", choices=sorted(_VIEWS))
    args = ap.parse_args(argv)
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), make_handler(args.bundle, args.events, args.view)
    )
    print(server.server_address[1], flush=True)  # parent reads this to open the browser
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
