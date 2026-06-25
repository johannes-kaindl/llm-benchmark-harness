"""FastAPI control-center. create_app() wires the 7 station routes over the reuse layer.
Templates/static are mounted from this package; routes return HTMX-friendly HTML."""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from touchstone import aggregate as aggregate_mod
from touchstone.config import load_config, models_from_json
from touchstone.gui import bundles, compare
from touchstone.gui import configs as configs_mod
from touchstone.gui import glossary as _glossary
from touchstone.gui import packs as packs_mod
from touchstone.gui.control import RunRegistry
from touchstone.pack import load_pack

_PKG = Path(__file__).parent
_templates = Jinja2Templates(directory=str(_PKG / "templates"))
_templates.env.globals["g"] = _glossary.describe  # g("ttft_p50").short in templates

# Hosts allowed by the DNS-rebinding guard. The GUI binds to 127.0.0.1 and is
# single-user; "testserver" is the host the Starlette TestClient uses.
_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "*.localhost", "testserver"]
# Hostnames considered "the local server" for the CSRF Origin/Referer check.
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "testserver"}


def render(name: str, request: Request, **ctx: Any) -> HTMLResponse:
    """Module-level render helper so tests can monkeypatch it to spy on template ctx."""
    return _templates.TemplateResponse(request, name, ctx)


def _is_local_origin(value: str) -> bool:
    """True iff an Origin/Referer URL points at the local server (hostname only)."""
    from urllib.parse import urlsplit

    host = urlsplit(value).hostname or ""
    return host in _LOCAL_HOSTNAMES or host.endswith(".localhost")


def create_app(*, runs_dir: Path, registry: RunRegistry) -> FastAPI:
    app = FastAPI(title="touchstone", docs_url=None, redoc_url=None)
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
        return render("pack.html", request, pack=pk, path=pack_path, active="pack")

    _MAX_PACK_YAML = 1_000_000  # a pack YAML over ~1 MB is abuse, not a use case

    @app.get("/pack-editor", response_class=HTMLResponse)
    def pack_editor(request: Request, path: str | None = None) -> HTMLResponse:
        """Guided YAML editor. With a valid ?path the file's RAW text seeds the textarea;
        otherwise the minimal NEW_PACK_TEMPLATE. Validation/preview/save are separate routes."""
        offered = sorted(str(p) for p in Path("packs").glob("*.yaml"))
        if path is not None:
            if path not in set(offered):  # confine to the packs the editor actually offers
                raise HTTPException(status_code=404)
            try:
                yaml_text = Path(path).read_text(encoding="utf-8")
            except (FileNotFoundError, OSError):
                raise HTTPException(status_code=404) from None
        else:
            yaml_text = packs_mod.NEW_PACK_TEMPLATE
        return render(
            "pack_editor.html",
            request,
            yaml_text=yaml_text,
            path=path,
            packs=offered,
            active="pack",
        )

    @app.post("/packs/validate")
    def packs_validate(yaml_text: str = Form(...)) -> dict[str, Any]:
        """Validate edited pack YAML through the pydantic contract; render the viewer body as
        a preview on success. Never 500s — a parse/schema error is a normal {ok:false} result."""
        if len(yaml_text) > _MAX_PACK_YAML:
            raise HTTPException(status_code=400, detail="pack YAML too large")
        result = packs_mod.validate_pack_yaml(yaml_text)
        preview_html = None
        if result["ok"]:
            preview_html = _templates.get_template("macros/_pack_body.html").render(
                pack=result["pack"]
            )
        return {
            "ok": result["ok"],
            "errors": result["errors"],
            "summary": result["summary"],
            "preview_html": preview_html,
        }

    @app.post("/packs/save")
    def packs_save(
        filename: str = Form(...),
        yaml_text: str = Form(...),
        overwrite: str = Form(""),
    ) -> dict[str, Any]:
        """Write a validated pack into packs/. Hard guards: confined filename, validate-before-
        write (never persist an invalid pack), overwrite must be explicit (409 otherwise)."""
        if len(yaml_text) > _MAX_PACK_YAML:
            raise HTTPException(status_code=400, detail="pack YAML too large")
        fname = packs_mod.safe_pack_filename(filename)
        if fname is None:
            raise HTTPException(status_code=400, detail="invalid filename")
        if not packs_mod.validate_pack_yaml(yaml_text)["ok"]:
            raise HTTPException(status_code=400, detail="pack does not validate")
        packs_dir = Path("packs").resolve()
        target = (packs_dir / fname).resolve()
        if not target.is_relative_to(packs_dir):  # defense in depth beyond the filename regex
            raise HTTPException(status_code=400, detail="invalid filename")
        packs_dir.mkdir(parents=True, exist_ok=True)
        if target.exists() and overwrite.strip().lower() not in {"true", "1", "on", "yes"}:
            raise HTTPException(status_code=409, detail="file exists")
        target.write_text(yaml_text, encoding="utf-8")
        return {"saved": fname}

    @app.get("/config-view/{config_path:path}", response_class=HTMLResponse)
    def config_view(request: Request, config_path: str) -> HTMLResponse:
        candidate = Path(config_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise HTTPException(status_code=404)
        if config_path not in {str(p) for p in Path(".").glob("config*.yaml")}:
            raise HTTPException(status_code=404)
        try:
            cfg = load_config(config_path)
        except (FileNotFoundError, OSError):
            raise HTTPException(status_code=404) from None
        return render("config_view.html", request, cfg=cfg, path=config_path, active="config")

    @app.get("/export-yaml")
    def export_yaml(kind: str, path: str) -> Any:
        """Serialize the *effective* (validated) config or pack back to YAML for download.
        Path is confined to the exact globs the pickers offer — never an arbitrary cwd file."""
        if kind == "config":
            offered = {str(p) for p in Path(".").glob("config*.yaml")}
            loader: Any = load_config
        elif kind == "pack":
            offered = {str(p) for p in Path("packs").glob("*.yaml")}
            loader = load_pack
        else:
            raise HTTPException(status_code=404)
        if path not in offered:
            raise HTTPException(status_code=404)
        try:
            model = loader(path)
        except (FileNotFoundError, OSError, ValueError):
            raise HTTPException(status_code=404) from None
        data = model.model_dump(mode="json")
        if kind == "config":
            # Never ship the endpoint api_key in a downloadable artifact (the on-screen
            # config viewer masks it too — the download must not be a side-channel leak).
            ep = data.get("endpoint")
            if isinstance(ep, dict) and "api_key" in ep:
                ep["api_key"] = "<redacted>"
        body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        name = Path(path).name
        return Response(
            body,
            media_type="application/x-yaml",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/export-report/{name}")
    def export_report(name: str, judging: int = 1) -> Any:
        """The complete bundle as one internally-linked Obsidian Markdown report. With
        `judging=0` the qualitative judgement is stripped and a Bewertungs-Auftrag (fillable
        scorecard + instructions) is embedded instead — to hand to a cloud AI for evaluation."""
        from touchstone.gui.glossary import GLOSSARY
        from touchstone.gui.report_md import render_report_md

        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not rd.is_dir():
            raise HTTPException(status_code=404)
        detail = bundles.bundle_detail(rd)
        if detail is None:
            raise HTTPException(status_code=404)
        include = bool(judging)
        md = render_report_md(detail, GLOSSARY, include_judging=include)
        fname = f"{name}.md" if include else f"{name}-zum-bewerten.md"
        return Response(
            md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    @app.get("/result/{name}", response_class=HTMLResponse)
    def result(
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
        try:
            detail = bundles.bundle_detail(rd)
            summary = bundles.classify(rd)
        except Exception:
            detail, summary = None, bundles.BundleSummary(run_dir=rd, status="error")
        axis_opts = (
            compare.axis_options(detail["responses"])
            if detail and detail.get("responses")
            else None
        )
        projection = variant or model
        # Compute compare_detail when the bundle is comparable OR when an axis was
        # explicitly requested (so the single-axis "nichts zu vergleichen" message renders).
        cmp = (
            compare.compare_detail(rd, axis, projection=projection, base=detail)
            if detail and axis_opts and (axis_opts.comparable or axis is not None)
            else None
        )
        if detail and detail.get("responses") and detail.get("master_rows") is not None:
            answer_cells, answer_default_cell = compare.answer_filter_cells(
                detail["responses"], detail["master_rows"]
            )
        else:
            answer_cells, answer_default_cell = [], "__all__"
        return render(
            "result.html",
            request,
            detail=detail,
            summary=summary,
            run_dir=rd,
            compare_opts=axis_opts,
            compare_detail=cmp,
            axis_opts=axis_opts,
            answer_cells=answer_cells,
            answer_default_cell=answer_default_cell,
            active="overview",
        )

    @app.get("/compare", response_class=HTMLResponse)
    def compare_cross(
        request: Request, rows: list[str] | None = Query(default=None)
    ) -> HTMLResponse:
        pool = aggregate_mod.pool_rows(runs_dir)
        diff = None
        if rows:
            wanted = [x for x in rows if x]
            selected = [r for r in pool if r.id in wanted]
            # Preserve the user's selection order (as given in ?rows=).
            selected.sort(key=lambda r: wanted.index(r.id))
            if len(selected) >= 2:
                diff = aggregate_mod.diff_rows(selected)
        return render("compare.html", request, pool=pool, diff=diff, active="compare")

    @app.get("/compare/{name}")
    def compare_axis(request: Request, name: str) -> RedirectResponse:
        qs = request.url.query
        target = f"/result/{name}" + (f"?{qs}" if qs else "")
        return RedirectResponse(target, status_code=301)

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

    @app.get("/eval-model-options")
    def eval_model_options(config: str) -> dict[str, Any]:
        """Single-select model options for Eval-start: the endpoint's actually-served models
        merged with the config's declared ones (which carry the thinking knobs), plus a default.
        Never 500s — a dead endpoint falls back to the config models with the error attached."""
        if config not in {str(p) for p in Path(".").glob("config*.yaml")}:
            raise HTTPException(status_code=404)
        disc = configs_mod.discover_endpoint_models(config)
        opts = configs_mod.eval_model_options(
            config_models=[m.model_dump() for m in configs_mod.config_models(config)],
            endpoint_models=disc["models"],
        )
        return {
            "options": opts["options"],
            "default_id": opts["default_id"],
            "error": disc["error"],
        }

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

    @app.get("/export-bundle/{name}")
    def export_bundle(name: str) -> Any:
        rd = (runs_dir / name).resolve()
        if not rd.is_relative_to(runs_dir.resolve()) or not (rd / "bundle.json").exists():
            raise HTTPException(status_code=404)
        ledger = [
            "bundle.json",
            "responses.jsonl",
            "scores.csv",
            "reports.jsonl",
            "judgements.jsonl",
            "scorecard.md",
            "perf.csv",
            "resources.jsonl",
        ]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in ledger:
                p = rd / f
                if p.exists():
                    z.write(p, arcname=f)
        buf.seek(0)
        return Response(
            buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
        )

    @app.post("/import-bundle")
    def import_bundle(file: UploadFile = File(...)) -> Any:
        """Accept a zipped bundle from another machine, validate it, and land it under
        runs_dir under a collision-safe name. Multi-machine aggregation: a bundle is
        bundle.json + responses.jsonl + scores.csv — portable, self-contained."""
        import shutil
        import tempfile

        rdir = runs_dir.resolve()
        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(tmp) / "extracted"
            extracted.mkdir()
            max_total = 500 * 1024 * 1024  # decompressed budget for an untrusted bundle
            max_members = 10_000
            raw = file.file.read(max_total + 1)
            if len(raw) > max_total:
                raise HTTPException(status_code=400, detail="upload too large")
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="not a zip file") from None
            with zf as z:
                # Reject any member that would escape the extraction root (zip-slip).
                for member in z.namelist():
                    dest = (extracted / member).resolve()
                    if not dest.is_relative_to(extracted.resolve()):
                        raise HTTPException(status_code=400, detail="unsafe zip entry")
                # Reject zip-bombs before extracting untrusted input: a tiny upload can
                # inflate to gigabytes. Cap member count and total uncompressed size.
                infos = z.infolist()
                if len(infos) > max_members:
                    raise HTTPException(status_code=400, detail="too many zip entries")
                if sum(i.file_size for i in infos) > max_total:
                    raise HTTPException(status_code=400, detail="zip too large")
                z.extractall(extracted)

            if not (extracted / "bundle.json").exists():
                raise HTTPException(status_code=400, detail="missing bundle.json")
            try:
                json.loads((extracted / "bundle.json").read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raise HTTPException(status_code=400, detail="invalid bundle.json") from None
            resp = extracted / "responses.jsonl"
            if not resp.exists():
                raise HTTPException(status_code=400, detail="missing responses.jsonl")
            try:
                for line in resp.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        json.loads(line)
            except (json.JSONDecodeError, OSError):
                raise HTTPException(status_code=400, detail="invalid responses.jsonl") from None

            stem = Path(file.filename or "bundle").stem or "bundle"
            name = stem
            n = 2
            while (rdir / name).exists():
                name = f"{stem}__{n}"
                n += 1
            shutil.move(str(extracted), str(rdir / name))
        return {"run_dir": name}

    _register_control_routes(app, runs_dir=runs_dir, registry=registry)
    return app


def _register_control_routes(app: FastAPI, *, runs_dir: Path, registry: RunRegistry) -> None:
    """Station 3 (control) + Station 4 (live SSE): POST endpoints drive the registry;
    GET /live/{name} streams the folded view as Server-Sent Events."""
    from touchstone.gui import live as live_mod
    from touchstone.gui.control import RunHandle, RunInProgress, is_active, read_sentinel
    from touchstone.webmon import POLL_S

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
    """Entry point for `touchstone gui`: build the app, bind, optionally open the browser."""
    import socket
    import threading
    import webbrowser

    import uvicorn

    from touchstone.gui.control import RealProcessLauncher

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
