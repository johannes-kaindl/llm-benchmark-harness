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
from ramcheck import loadview as loadview_mod
from ramcheck import tail as tail_mod

POLL_S = 0.25

INDEX_HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>ramcheck monitor</title>
<style>
 body{font-family:system-ui,sans-serif;margin:1.5rem;background:#111;color:#eee}
 h1{font-size:1.1rem} .bar{background:#333;border-radius:4px;height:1.2rem;overflow:hidden}
 .bar>div{background:#3a7;height:100%;width:0;transition:width .3s}
 .grid{display:flex;gap:1.5rem;margin:1rem 0;flex-wrap:wrap}
 .card{background:#1b1b1b;padding:.7rem 1rem;border-radius:6px;min-width:7rem}
 .num{font-size:1.3rem;font-weight:600}
 table{border-collapse:collapse;width:100%;font-size:.85rem;margin-top:.5rem}
 td,th{padding:.25rem .5rem;border-bottom:1px solid #2a2a2a;text-align:left}
 .ok{color:#5c5} .fail{color:#e66} .muted{color:#999}
</style></head>
<body>
<h1>ramcheck — live eval monitor</h1>
<div class="bar"><div id="barfill"></div></div>
<div class="grid">
 <div class="card"><div class="muted">Fortschritt</div><div class="num"><span id="done">0</span>/<span id="total">0</span></div></div>
 <div class="card"><div class="muted">ok / Fehler</div><div class="num"><span class="ok" id="ok">0</span> / <span class="fail" id="failed">0</span></div></div>
 <div class="card"><div class="muted">ETA</div><div class="num" id="eta">–</div></div>
 <div class="card"><div class="muted">RAM</div><div class="num" id="ram">–</div></div>
 <div class="card"><div class="muted">Pressure</div><div class="num" id="pressure">–</div></div>
 <div class="card"><div class="muted">Throttle</div><div class="num" id="throttle">–</div></div>
</div>
<table><thead><tr><th>#</th><th>Modell</th><th>Variante</th><th>Prompt</th><th>Status</th><th>TTFT</th><th>tok/s</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
function fmtEta(s){if(s==null)return '–';s=Math.round(s);return Math.floor(s/60)+'m '+(s%60)+'s';}
const es=new EventSource('/events');
es.addEventListener('view',e=>{let v;try{v=JSON.parse(e.data)}catch(_){return}
 done.textContent=v.done; total.textContent=v.total; ok.textContent=v.ok; failed.textContent=v.failed;
 eta.textContent=v.finished?'fertig':fmtEta(v.eta_s);
 barfill.style.width=(v.total?100*v.done/v.total:0)+'%';
 rows.innerHTML=v.cells.slice().reverse().map(c=>{
  const st=c.status==='done'?(c.ok?'<span class="ok">✓</span>':'<span class="fail">✗</span>'):'<span class="muted">…</span>';
  const tt=c.ttft_s!=null?c.ttft_s.toFixed(2)+'s':''; const dc=c.decode_tps!=null?c.decode_tps.toFixed(1):'';
  const th=c.reasoning_chars>0?` <span class="muted" title="${c.reasoning_chars} reasoning chars">💭</span>`:'';
  return `<tr><td>${c.i}</td><td>${c.model}</td><td>${c.variant}</td><td>${c.prompt_id}${th}</td><td>${st}</td><td>${tt}</td><td>${dc}</td></tr>`;
 }).join('');});
es.addEventListener('load',e=>{let l;try{l=JSON.parse(e.data)}catch(_){return}
 ram.textContent=l.sys_used_mb!=null?Math.round(l.sys_used_mb)+' MB':'–';
 pressure.textContent=l.mem_pressure||'–';
 throttle.textContent=l.throttled?'JA':(l.any_throttle_seen?'nein':'n/v');});
es.onerror=()=>{document.title='ramcheck monitor (offline)';};
</script></body></html>"""


def make_handler(
    bundle: str | Path, events_name: str = "events.jsonl"
) -> type[BaseHTTPRequestHandler]:
    bundle_dir = Path(bundle)
    events_path = bundle_dir / events_name
    resources_path = bundle_dir / "resources.jsonl"

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
            body = INDEX_HTML.encode("utf-8")
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
                        parsed = events_mod.parse_line(ln)
                        if parsed is not None:
                            all_events.append(parsed)
                    view = events_mod.build_view(all_events)
                    self._send("view", json.dumps(view.as_dict()))
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
    args = ap.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.bundle, args.events))
    print(server.server_address[1], flush=True)  # parent reads this to open the browser
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
