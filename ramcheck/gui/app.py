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
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ramcheck import aggregate as aggregate_mod
from ramcheck.config import models_from_json
from ramcheck.gui import bundles, compare
from ramcheck.gui import configs as configs_mod
from ramcheck.gui.control import RunRegistry
from ramcheck.pack import load_pack

_PKG = Path(__file__).parent
_templates = Jinja2Templates(directory=str(_PKG / "templates"))

# Hosts allowed by the DNS-rebinding guard. The GUI binds to 127.0.0.1 and is
# single-user; "testserver" is the host the Starlette TestClient uses.
_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "*.localhost", "testserver"]
# Hostnames considered "the local server" for the CSRF Origin/Referer check.
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "testserver"}


def _is_local_origin(value: str) -> bool:
    """True iff an Origin/Referer URL points at the local server (hostname only)."""
    from urllib.parse import urlsplit

    host = urlsplit(value).hostname or ""
    return host in _LOCAL_HOSTNAMES or host.endswith(".localhost")


def create_app(*, runs_dir: Path, registry: RunRegistry) -> FastAPI:
    app = FastAPI(title="ramcheck", docs_url=None, redoc_url=None)
    # DNS-rebinding defense: only localhost/127.0.0.1 Host headers are accepted.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)

    @app.middleware("http")
    async def _csrf_origin_guard(request: Request, call_next: Any) -> Any:
        # CSRF defense: a state-changing request carrying an Origin/Referer that is not
        # the local server is rejected. Lightweight — same-origin form posts (and the
        # TestClient, which sends neither header) are unaffected.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            probe = origin or referer
            if probe is not None and not _is_local_origin(probe):
                from starlette.responses import PlainTextResponse

                return PlainTextResponse("cross-origin request rejected", status_code=403)
        return await call_next(request)

    static_dir = _PKG / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def render(name: str, request: Request, **ctx: Any) -> HTMLResponse:
        return _templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> HTMLResponse:
        items = bundles.discover(runs_dir)
        compare_links = {b.run_dir.name: compare.axis_options_for_dir(b.run_dir) for b in items}
        return render(
            "overview.html", request, bundles=items, compare_links=compare_links, active="overview"
        )

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
        return render("pack.html", request, pack=pk, active="pack")

    @app.get("/result/{name}", response_class=HTMLResponse)
    def result(request: Request, name: str) -> HTMLResponse:
        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
            raise HTTPException(status_code=404)
        try:
            detail = bundles.bundle_detail(rd)
            summary = bundles.classify(rd)
        except Exception:
            detail, summary = None, bundles.BundleSummary(run_dir=rd, status="error")
        compare_opts = (
            compare.axis_options(detail["responses"])
            if detail and detail.get("responses")
            else None
        )
        return render(
            "result.html",
            request,
            detail=detail,
            summary=summary,
            run_dir=rd,
            compare_opts=compare_opts,
            active="overview",
        )

    @app.get("/compare", response_class=HTMLResponse)
    def compare_cross(request: Request) -> HTMLResponse:
        from ramcheck.gui.hwlabel import label_mismatch

        rows = aggregate_mod.load_all_scores(runs_dir)
        agg = aggregate_mod.aggregate(rows) if rows else []
        flagged = [
            (a, label_mismatch(chip=a.chip, ram_gb=str(a.ram_gb), machine=a.machine)) for a in agg
        ]
        return render("compare.html", request, rows=flagged, active="compare")

    @app.get("/compare/{name}", response_class=HTMLResponse)
    def compare_axis(
        request: Request,
        name: str,
        axis: str | None = None,
        variant: str | None = None,
        model: str | None = None,
    ) -> HTMLResponse:
        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
            raise HTTPException(status_code=404)
        if axis is not None and axis not in ("model", "variant"):
            raise HTTPException(status_code=422)
        projection = variant or model  # generated links only ever set the axis-relevant one
        try:
            detail = compare.compare_detail(rd, axis, projection=projection)
        except Exception:
            detail = None
        return render("compare_axis.html", request, detail=detail, run_dir=rd, active="overview")

    @app.get("/config", response_class=HTMLResponse)
    def config_get(
        request: Request,
        resume: str | None = None,
        bundle: str | None = None,
        conflict: bool = False,
    ) -> HTMLResponse:
        """Station 3: configuration + run-start form."""
        packs_dir = Path("packs")
        pack_files = sorted(str(p) for p in packs_dir.glob("*.yaml")) if packs_dir.exists() else []
        config_files = configs_mod.order_configs([str(p) for p in Path(".").glob("config*.yaml")])
        judge_config_files = sorted(str(p) for p in Path(".").glob("judge*.yaml"))
        eval_only = [b.run_dir.name for b in bundles.discover(runs_dir) if b.status == "eval-only"]
        return render(
            "config.html",
            request,
            packs=pack_files,
            configs=config_files,
            models_by_config=configs_mod.models_by_config(config_files),
            judge_configs=judge_config_files,
            eval_only_bundles=eval_only,
            resume=resume,
            bundle=bundle,
            conflict=conflict,
            error=None,
            active="config",
        )

    @app.get("/endpoint-models")
    def endpoint_models(config: str) -> dict[str, Any]:
        """Models the selected config's endpoint advertises (/v1/models). Never 500s —
        a dead endpoint returns {"models": [], "error": "..."}."""
        # Restrict to the config*.yaml files the picker actually offers — this rejects path
        # traversal AND prevents reading (and error-echoing the parsed content of) any other
        # cwd YAML.
        if config not in {str(p) for p in Path(".").glob("config*.yaml")}:
            raise HTTPException(status_code=404)
        return configs_mod.discover_endpoint_models(config)

    @app.get("/judge-endpoint-models")
    def judge_endpoint_models(judge_config: str) -> dict[str, Any]:
        """Models the selected judge config's endpoint advertises. Never 500s. Same path guard
        as /endpoint-models: only the judge*.yaml files the picker actually offers."""
        if judge_config not in {str(p) for p in Path(".").glob("judge*.yaml")}:
            raise HTTPException(status_code=404)
        return configs_mod.discover_judge_endpoint_models(judge_config)

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

    _register_control_routes(app, runs_dir=runs_dir, registry=registry)
    return app


def _register_control_routes(app: FastAPI, *, runs_dir: Path, registry: RunRegistry) -> None:
    """Station 3 (control) + Station 4 (live SSE): POST endpoints drive the registry;
    GET /live/{name} streams the folded view as Server-Sent Events."""
    from ramcheck.gui import live as live_mod
    from ramcheck.gui.control import RunHandle, RunInProgress, is_active, read_sentinel
    from ramcheck.webmon import POLL_S

    # Once a run is no longer alive and no terminal event has arrived, stop after this
    # many idle snapshots (no new bytes) so a crashed subprocess can't busy-loop forever.
    IDLE_CAP = 3

    _runs_dir = runs_dir.resolve()

    def _confine(name: str) -> Path:
        """Resolve name to a path confined within runs_dir; raise 404 on traversal."""
        candidate = (_runs_dir / name).resolve()
        if not candidate.is_relative_to(_runs_dir):
            raise HTTPException(status_code=404)
        return candidate

    def _confine_cwd(rel: str) -> str:
        """Confine a cwd-rooted input (pack/config path): reject absolute or traversing
        paths, mirroring the /packs guard. Returns the path unchanged when allowed."""
        candidate = Path(rel)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise HTTPException(status_code=404)
        return rel

    @app.post("/runs/eval")
    def start_eval(
        pack_path: str = Form(...),
        config_path: str = Form(...),
        resume_dir: str | None = Form(None),
        models_json: str = Form(""),
    ) -> Any:
        pack_path = _confine_cwd(pack_path)
        config_path = _confine_cwd(config_path)
        # "Fortsetzen": confine the posted resume dir under runs_dir and pass it through
        # so the registry adds --resume and reuses the dir instead of starting fresh.
        resume: Path | None = _confine(resume_dir) if resume_dir else None
        # Resume never overrides models (the bundle's cells are fixed); otherwise apply
        # the picker selection. Invalid/empty selection must not spawn a run.
        models = None
        if resume is None and models_json.strip():
            try:
                models = models_from_json(models_json)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from None
        try:
            h = registry.start_eval(
                pack_path=pack_path, config_path=config_path, resume_dir=resume, models=models
            )
        except RunInProgress as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"run_dir": h.run_dir.name, "kind": h.kind}

    @app.post("/runs/judge")
    def start_judge(
        bundle: str = Form(...),
        judge_config_path: str = Form(...),
        judge_model: str = Form(""),
    ) -> Any:
        bundle_dir = _confine(bundle)
        try:
            h = registry.start_judge(
                bundle=bundle_dir, judge_config_path=judge_config_path, judge_model=judge_model
            )
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
            idle = 0
            try:
                while True:
                    prev_offset = stream._offset
                    view = stream.snapshot()
                    yield f"event: view\ndata: {json.dumps(view)}\n\n"
                    if view.get("finished"):
                        break
                    # No terminal event arrived. If the run is no longer alive (its
                    # sentinel pid is dead), the subprocess crashed without writing
                    # run_done — emit a terminal 'crashed' frame and stop rather than
                    # busy-looping forever, leaking a server thread (MAJOR 2).
                    if not is_active(read_sentinel(run_dir)):
                        # Allow a few idle reads so any final flushed bytes are picked
                        # up; once no new bytes arrive, mark crashed and break.
                        if stream._offset == prev_offset:
                            idle += 1
                        else:
                            idle = 0
                        if idle >= IDLE_CAP:
                            view["finished"] = True
                            view["crashed"] = True
                            yield f"event: view\ndata: {json.dumps(view)}\n\n"
                            break
                    else:
                        idle = 0
                    time.sleep(POLL_S)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )


def serve(*, runs_dir: Path, port: int = 0, open_browser: bool = True) -> None:  # pragma: no cover
    """Entry point for `ramcheck gui`: build the app, bind, optionally open the browser."""
    import socket
    import threading
    import webbrowser

    import uvicorn

    from ramcheck.gui.control import RealProcessLauncher

    registry = RunRegistry(runs_dir=runs_dir, launcher=RealProcessLauncher())
    application = create_app(runs_dir=runs_dir, registry=registry)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    bound_port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{bound_port}"
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    config = uvicorn.Config(application, log_level="warning")
    server = uvicorn.Server(config)
    server.run(sockets=[sock])
