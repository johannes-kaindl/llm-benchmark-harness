import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from ramcheck import webmon
from ramcheck.runner import _WebMonitorProcess


def _bundle(tmp_path):
    (tmp_path / "events.jsonl").write_text(
        json.dumps({"ts": 1.0, "type": "run_start", "total": 2})
        + "\n"
        + json.dumps(
            {
                "ts": 2.0,
                "type": "cell_done",
                "i": 0,
                "model": "m",
                "variant": "v",
                "prompt_id": "p",
                "repeat": 0,
                "ok": True,
                "ttft_s": 0.3,
                "e2e_s": 1.0,
                "decode_tps": 5.0,
                "completion_tokens": 3,
                "content_empty": False,
                "error": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "resources.jsonl").write_text(
        json.dumps(
            {"ts": 1.5, "sys_used_mb": 1234.0, "mem_pressure_level": "normal", "throttled": False}
        )
        + "\n",
        encoding="utf-8",
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), webmon.make_handler(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_index_serves_html_with_eventsource(tmp_path):
    srv = _bundle(tmp_path)
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=3)
        c.request("GET", "/")
        r = c.getresponse()
        body = r.read().decode("utf-8")
        assert r.status == 200
        assert "text/html" in r.getheader("Content-Type", "")
        assert "EventSource" in body
    finally:
        srv.shutdown()


def test_sse_streams_view_and_load(tmp_path):
    srv = _bundle(tmp_path)
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("GET", "/events")
        r = c.getresponse()
        buf = b""
        for _ in range(40):  # poll is ~0.25s; first frame arrives within a tick
            buf += r.read(256)
            if b"event: view" in buf and b"event: load" in buf:
                break
        text = buf.decode("utf-8", errors="replace")
        assert "event: view" in text and "event: load" in text
        # pull the first `view` data payload and check it parsed correctly
        view_line = next(
            ln
            for blk in text.split("\n\n")
            if "event: view" in blk
            for ln in blk.split("\n")
            if ln.startswith("data: ")
        )
        view = json.loads(view_line[len("data: ") :])
        assert view["total"] == 2 and view["done"] == 1 and view["ok"] == 1
    finally:
        c.close()
        srv.shutdown()


def test_webmonitor_process_spawns_and_serves(tmp_path):
    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")
    mon = _WebMonitorProcess(tmp_path, port=0)
    port = mon.start()
    try:
        assert isinstance(port, int) and port > 0
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("GET", "/")
        assert c.getresponse().status == 200
    finally:
        mon.stop()
