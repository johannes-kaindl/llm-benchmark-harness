"""FastAPI control-center. create_app() wires the 7 station routes over the reuse layer.
Templates/static are mounted from this package; routes return HTMX-friendly HTML."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ramcheck import aggregate as aggregate_mod
from ramcheck.gui import bundles
from ramcheck.gui.control import RunRegistry
from ramcheck.pack import load_pack

_PKG = Path(__file__).parent
_templates = Jinja2Templates(directory=str(_PKG / "templates"))


def create_app(*, runs_dir: Path, registry: RunRegistry) -> FastAPI:
    app = FastAPI(title="ramcheck", docs_url=None, redoc_url=None)
    static_dir = _PKG / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def render(name: str, request: Request, **ctx: Any) -> HTMLResponse:
        return _templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> HTMLResponse:
        items = bundles.discover(runs_dir)
        return render("overview.html", request, bundles=items)

    @app.get("/packs/{pack_path:path}", response_class=HTMLResponse)
    def pack_explorer(request: Request, pack_path: str) -> HTMLResponse:
        # Confine to cwd-rooted paths only; reject absolute or traversing paths.
        candidate = Path(pack_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise HTTPException(status_code=404)
        try:
            pk = load_pack(pack_path)
        except (FileNotFoundError, OSError):
            raise HTTPException(status_code=404) from None
        return render("pack.html", request, pack=pk)

    @app.get("/result/{name}", response_class=HTMLResponse)
    def result(request: Request, name: str) -> HTMLResponse:
        # Confine name to a direct child of runs_dir (no traversal).
        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
            raise HTTPException(status_code=404)
        summary = bundles.classify(rd)
        return render("result.html", request, summary=summary, run_dir=rd)

    @app.get("/compare", response_class=HTMLResponse)
    def compare(request: Request) -> HTMLResponse:
        rows = aggregate_mod.load_all_scores(runs_dir)
        agg = aggregate_mod.aggregate(rows) if rows else []
        return render("compare.html", request, rows=agg)

    @app.get("/export/{name}/{fname}")
    def export(name: str, fname: str) -> Any:
        # Only ledger files are exportable (transient event/sentinel files excluded — G10).
        allowed = {"scorecard.md", "scores.csv", "perf.csv", "report.md", "aggregate.md"}
        if fname not in allowed:
            raise HTTPException(status_code=404)
        # Resolve and confine to runs_dir to prevent path traversal (e.g. %2e%2e segments).
        candidate = (runs_dir / name / fname).resolve()
        if not candidate.is_relative_to(runs_dir.resolve()):
            raise HTTPException(status_code=404)
        if not candidate.exists():
            raise HTTPException(status_code=404)
        return FileResponse(candidate)

    _register_control_routes(app, runs_dir=runs_dir, registry=registry)  # Task 10
    return app


def _register_control_routes(app: FastAPI, *, runs_dir: Path, registry: RunRegistry) -> None:
    """Station 3 (control) + Station 4 (live SSE): POST endpoints drive the registry;
    GET /live/{name} streams the folded view as Server-Sent Events."""
    from ramcheck.gui import live as live_mod
    from ramcheck.gui.control import RunHandle, RunInProgress, read_sentinel
    from ramcheck.webmon import POLL_S

    _runs_dir = runs_dir.resolve()

    def _confine(name: str) -> Path:
        """Resolve name to a path confined within runs_dir; raise 404 on traversal."""
        candidate = (_runs_dir / name).resolve()
        if not candidate.is_relative_to(_runs_dir):
            raise HTTPException(status_code=404)
        return candidate

    @app.post("/runs/eval")
    def start_eval(pack_path: str = Form(...), config_path: str = Form(...)) -> Any:
        try:
            h = registry.start_eval(pack_path=pack_path, config_path=config_path)
        except RunInProgress as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"run_dir": h.run_dir.name, "kind": h.kind}

    @app.post("/runs/judge")
    def start_judge(bundle: str = Form(...), judge_config_path: str = Form(...)) -> Any:
        bundle_dir = _confine(bundle)
        try:
            h = registry.start_judge(bundle=bundle_dir, judge_config_path=judge_config_path)
        except RunInProgress as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"run_dir": h.run_dir.name, "kind": h.kind}

    @app.post("/runs/stop")
    def stop_run(name: str = Form(...)) -> Any:
        run_dir = _confine(name)
        s = read_sentinel(run_dir)
        if s is None:
            raise HTTPException(status_code=404)
        registry.stop(RunHandle(str(s["kind"]), run_dir, int(s["pid"])))
        return {"stopped": name}

    @app.get("/live/{name}")
    def live_stream(name: str, kind: str = "eval") -> StreamingResponse:
        run_dir = _confine(name)
        fname = "events.jsonl" if kind == "eval" else "judge_events.jsonl"
        stream = live_mod.LiveStream(run_dir / fname, kind=kind)

        def gen() -> Any:
            try:
                while True:
                    view = stream.snapshot()
                    yield f"event: view\ndata: {json.dumps(view)}\n\n"
                    if view.get("finished"):
                        break
                    time.sleep(POLL_S)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )


def serve(*, runs_dir: Path, port: int = 0, open_browser: bool = True) -> None:  # pragma: no cover
    """Entry point for `ramcheck gui`: build the app, bind, optionally open the browser.
    (Fully implemented in Task 11.)"""
    raise NotImplementedError("GUI server not yet implemented — coming in Task 11")
