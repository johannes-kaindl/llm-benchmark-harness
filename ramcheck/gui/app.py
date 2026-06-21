"""FastAPI control-center. create_app() wires the 7 station routes over the reuse layer.
Templates/static are mounted from this package; routes return HTMX-friendly HTML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
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
        pk = load_pack(pack_path)
        return render("pack.html", request, pack=pk)

    @app.get("/result/{name}", response_class=HTMLResponse)
    def result(request: Request, name: str) -> HTMLResponse:
        rd = runs_dir / name
        summary = bundles.classify(rd)
        return render("result.html", request, summary=summary, run_dir=rd)

    @app.get("/compare", response_class=HTMLResponse)
    def compare(request: Request) -> HTMLResponse:
        rows = aggregate_mod.load_all_scores(runs_dir)
        agg = aggregate_mod.aggregate(rows) if rows else []
        return render("compare.html", request, rows=agg)

    @app.get("/export/{name}/{fname}")
    def export(name: str, fname: str) -> Any:
        from fastapi.responses import FileResponse

        # only ledger files are exportable (transient event/sentinel files excluded — G10)
        allowed = {"scorecard.md", "scores.csv", "perf.csv", "report.md", "aggregate.md"}
        if fname not in allowed:
            from fastapi import HTTPException

            raise HTTPException(status_code=404)
        return FileResponse(runs_dir / name / fname)

    _register_control_routes(app, runs_dir=runs_dir, registry=registry)  # Task 10
    return app


def _register_control_routes(app: FastAPI, *, runs_dir: Path, registry: RunRegistry) -> None:
    """No-op stub; replaced by Task 10."""


def serve(*, runs_dir: Path, port: int = 0, open_browser: bool = True) -> None:  # pragma: no cover
    """Entry point for `ramcheck gui`: build the app, bind, optionally open the browser.
    (Fully implemented in Task 11.)"""
    raise NotImplementedError("GUI server not yet implemented — coming in Task 11")
