"""touchstone CLI — run · embed · report.

touchstone run    --config config.m1.yaml    # M1: gegen LM Studio
touchstone run    --config config.m5.yaml    # M5: gegen mlx_lm.server / mlx-openai-server
touchstone embed  --config config.m5.yaml    # Embedding-Durchsatz separat
touchstone report --runs ./runs              # report.md (re)generieren
"""

from __future__ import annotations

import contextlib
import csv
import json
import sys
import time
import webbrowser
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from touchstone import aggregate as aggregate_mod
from touchstone import events as events_mod
from touchstone import hostinfo, report
from touchstone import judge_events as judge_events_mod
from touchstone import runqueue as rq
from touchstone import scorecard as scorecard_mod
from touchstone.client import OpenAIStreamClient
from touchstone.config import Config, apply_models_override, load_config
from touchstone.embed import render_embed_md, run_embed
from touchstone.judge import (
    JudgeAborted,
    JudgeBackend,
    JudgeConfig,
    OpenAIJudgeBackend,
    judge_bundle,
    load_judge_config,
    load_judgements_jsonl,
    write_reports_jsonl,
)
from touchstone.models import RunRecord
from touchstone.pack import Pack, load_pack
from touchstone.preflight import PreflightResult
from touchstone.qualrun import EvalCell, load_responses_jsonl, run_eval
from touchstone.report import load_raw_csv
from touchstone.results import EvalResponse, ModelReport, Verdict
from touchstone.runner import Cell, _WebMonitorProcess, resolve_engine, run_benchmark

app = typer.Typer(add_completion=False, help="Thin local-LLM benchmark harness.")
console = Console()


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _make_client(cfg: Config) -> OpenAIStreamClient:
    engine = cfg.engine or resolve_engine(cfg.endpoint.base_url)
    return OpenAIStreamClient(
        cfg.endpoint.base_url,
        cfg.endpoint.api_key,
        engine=engine,
        engine_version=cfg.engine_version or "unknown",
    )


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c", exists=True, help="YAML config"),
    out: Path | None = typer.Option(None, "--out", help="override output_dir"),
    settle: float = typer.Option(
        1.0, "--settle", help="seconds to let the sampler settle before requests"
    ),
    web: bool = typer.Option(False, "--web", help="live browser monitor for this run"),
    port: int = typer.Option(0, "--port", help="monitor port (0 = auto)"),
    no_open: bool = typer.Option(False, "--no-open", help="don't auto-open the browser"),
) -> None:
    """Run the chat benchmark matrix and write report.md + raw.csv."""
    cfg = load_config(config)
    base_out = out or cfg.output_path()
    run_dir = base_out / _timestamp()
    console.print(
        f"[bold]touchstone run[/] → [cyan]{run_dir}[/]  (engine: {cfg.engine or resolve_engine(cfg.endpoint.base_url)})"
    )
    if cfg.runs_per_cell < 8:
        console.print(
            f"[yellow]⚠️  runs_per_cell={cfg.runs_per_cell} < 8[/] — nach Warmup-/Throttle-/Akku-Ausschluss "
            "können Zellen unter die Validitätsgrenze (≥7 gewertet) fallen; betroffene Zellen werden im Report mit ⚠️ markiert."
        )

    client = _make_client(cfg)
    if not web:
        records = run_benchmark(cfg, client, run_dir=run_dir, settle_s=settle)
        _finalize_run_report(records, run_dir)
        return

    run_dir.mkdir(parents=True, exist_ok=True)
    with _live_monitor(run_dir, port, no_open) as (monitor, url):
        on_run_start, on_cell_start, on_cell_done, run_done = _run_event_writers(
            run_dir / "events.jsonl"
        )
        records = []
        try:
            records = run_benchmark(
                cfg,
                client,
                run_dir=run_dir,
                settle_s=settle,
                on_run_start=on_run_start,
                on_cell_start=on_cell_start,
                on_cell_done=on_cell_done,
            )
        finally:
            run_done(records)
        _finalize_run_report(records, run_dir)
        _hold_monitor(monitor, url)


@app.command()
def embed(
    config: Path = typer.Option(..., "--config", "-c", exists=True, help="YAML config"),
    out: Path | None = typer.Option(None, "--out", help="override output_dir"),
) -> None:
    """Measure embedding throughput against /v1/embeddings."""
    cfg = load_config(config)
    base_out = out or cfg.output_path()
    run_dir = base_out / f"{_timestamp()}_embed"
    console.print(f"[bold]touchstone embed[/] → [cyan]{run_dir}[/]  (model: {cfg.embed.model})")

    client = _make_client(cfg)
    result = run_embed(cfg, client, run_dir=run_dir)

    md = render_embed_md(result, date_str=_today())
    md_path = run_dir / "embed_report.md"
    md_path.write_text(md, encoding="utf-8")
    console.print(
        f"[green]✓[/] {result.embeddings_per_s:.1f} emb/s · {result.total_s:.1f}s · [bold]{md_path}[/]"
    )


@app.command(name="report")
def report_cmd(
    runs: Path = typer.Option(
        ..., "--runs", exists=True, help="runs dir (recursively collects raw.csv)"
    ),
) -> None:
    """(Re)generate report.md from every raw.csv found under --runs."""
    raw_files = sorted(runs.rglob("raw.csv"))
    if not raw_files:
        console.print(f"[red]Keine raw.csv unter {runs} gefunden.[/]")
        raise typer.Exit(code=1)

    records = []
    for f in raw_files:
        records.extend(load_raw_csv(f))

    md = report.render_report_md(records, date_str=_today(), host=hostinfo.summary())
    out_path = runs / "report.md"
    out_path.write_text(md, encoding="utf-8")
    console.print(
        f"[green]✓[/] {len(records)} Zeilen aus {len(raw_files)} raw.csv → [bold]{out_path}[/]"
    )


@app.command(name="aggregate")
def aggregate_cmd(
    runs: Path = typer.Option(
        ..., "--runs", exists=True, help="runs dir (recursively collects scores.csv)"
    ),
    out: Path | None = typer.Option(None, "--out", help="output dir (default: --runs dir)"),
) -> None:
    """Aggregate scores.csv across runs/machines → one Hardware×Quality table (md + csv)."""
    rows = aggregate_mod.load_all_scores(runs)
    if not rows:
        console.print(f"[red]Keine scores.csv unter {runs} — erst `touchstone judge` ausführen.[/]")
        raise typer.Exit(code=1)
    agg = aggregate_mod.aggregate(rows)
    out_dir = out or runs
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "aggregate.md"
    csv_path = out_dir / "scores_all.csv"
    md_path.write_text(aggregate_mod.render_aggregate_md(agg, date_str=_today()), encoding="utf-8")
    aggregate_mod.write_scores_all_csv(rows, csv_path)
    console.print(
        f"[green]✓[/] {len(rows)} Zeilen → {len(agg)} Gruppen → [bold]{md_path}[/] + {csv_path}"
    )


def _write_bundle_manifest(
    run_dir: Path, pack_path: Path, cfg: Config, host: dict[str, str]
) -> None:
    from touchstone.pack import load_pack as _lp

    pk = _lp(pack_path)
    manifest = {
        "pack_id": pk.id,
        "pack_version": pk.version,
        "pack_path": str(Path(pack_path).resolve()),
        # extra_body (e.g. reasoning_effort) is part of what was measured — and what resume reuses
        "models": [
            {"id": m.id, "quant": m.quant, **({"extra_body": m.extra_body} if m.extra_body else {})}
            for m in cfg.models
        ],
        "variants": [v.id for v in pk.prompt_variants],
        "host": host,
        "sampling": {"temperature": pk.sampling.temperature, "seed": pk.sampling.seed},
        "date": _today(),
    }
    (run_dir / "bundle.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@contextlib.contextmanager
def _live_monitor(
    run_dir: Path,
    port: int,
    no_open: bool,
    events_name: str = "events.jsonl",
    view: str = "eval",
) -> Iterator[tuple[_WebMonitorProcess, str | None]]:
    """Spawn the live-monitor subprocess, open the browser, and always stop it on exit."""
    monitor = _WebMonitorProcess(run_dir, port=port, events_name=events_name, view=view)
    bound = monitor.start()
    url = f"http://127.0.0.1:{bound}" if bound else None
    if url is not None:
        console.print(f"[bold]Monitor:[/] [cyan]{url}[/] [dim](Ctrl-C zum Beenden)[/]")
        if not no_open:
            webbrowser.open(url)
    else:
        console.print("[yellow]Web-Monitor konnte nicht starten — Lauf läuft ohne ihn.[/]")
    try:
        yield monitor, url
    finally:
        monitor.stop()


def _hold_monitor(monitor: _WebMonitorProcess, url: str | None) -> None:
    """After the run + finalize, keep the dashboard readable until the user presses Ctrl-C."""
    if url is not None:
        console.print(f"[dim]Monitor läuft weiter auf {url} — Ctrl-C zum Beenden.[/]")
        with contextlib.suppress(KeyboardInterrupt):
            monitor.wait()


def _eval_event_writers(
    events_path: Path,
    *,
    append: bool = True,
) -> tuple[
    Callable[[int], None],
    Callable[[int, EvalCell], None],
    Callable[[int, EvalResponse], None],
    Callable[[list[PreflightResult]], None],
    Callable[[list[EvalResponse]], None],
]:
    """Closures that translate run_eval's callbacks into events.jsonl lines.

    append=True keeps the historical --web semantics; append=False truncates so a
    GUI-spawned run starts a fresh stream (no stale run_done → no false 'finished')."""
    fh = events_path.open("a" if append else "w", encoding="utf-8")

    def _w(event: dict[str, object]) -> None:
        fh.write(events_mod.dumps(event) + "\n")
        fh.flush()

    def on_run_start(total: int) -> None:
        _w(events_mod.run_start_event(time.time(), total))

    def on_cell_start(i: int, cell: EvalCell) -> None:
        _w(
            events_mod.cell_start_event(
                time.time(),
                i,
                cell.model.id,
                cell.variant.id,
                cell.category.id,
                cell.prompt.id,
                cell.repeat,
            )
        )

    def on_cell_done(i: int, resp: EvalResponse) -> None:
        _w(
            events_mod.cell_done_event(
                time.time(),
                i,
                resp.model,
                resp.variant,
                resp.prompt_id,
                resp.repeat,
                resp.ok,
                resp.ttft_s,
                resp.e2e_s,
                resp.decode_tps,
                resp.completion_tokens,
                resp.content_empty,
                resp.error,
                resp.reasoning_chars,
            )
        )

    def on_preflight(results: list[PreflightResult]) -> None:
        _w(events_mod.preflight_event(time.time(), [r.as_dict() for r in results]))

    def run_done(responses: list[EvalResponse]) -> None:
        try:
            _w(
                events_mod.run_done_event(
                    time.time(), len(responses), sum(1 for r in responses if r.ok)
                )
            )
        finally:
            fh.close()  # close even if the final write fails (e.g. disk full)

    return on_run_start, on_cell_start, on_cell_done, on_preflight, run_done


def _judge_event_writers(
    events_path: Path,
) -> tuple[
    Callable[[int, list[Verdict]], None],
    Callable[[Verdict], None],
    Callable[[list[dict[str, object]]], None],
    Callable[[int, int], None],
]:
    """Closures translating the judge run into judge_events.jsonl lines.

    Returns (on_judge_start, on_verdict, write_masters, judge_done). ``on_judge_start``
    writes the total and replays prior verdicts (resume) so the dashboard seeds correctly.
    A shared counter gives every verdict a stable display index.
    Callers MUST call ``judge_done`` (e.g. in a ``finally`` block) — it closes the file handle."""
    # truncate: judge_events.jsonl is transient monitor instrumentation, not persistence
    # (that's judgements.jsonl). on_judge_start replays prior verdicts, so each --web run
    # starts a fresh, complete event stream. (eval keeps append for its resume semantics.)
    fh = events_path.open("w", encoding="utf-8")
    counter = {"i": 0}

    def _w(event: dict[str, object]) -> None:
        fh.write(judge_events_mod.dumps(event) + "\n")
        fh.flush()

    def _verdict(v: Verdict) -> None:
        i = counter["i"]
        counter["i"] += 1
        _w(
            judge_events_mod.verdict_event(
                time.time(),
                i,
                v.model,
                v.variant,
                v.prompt_id,
                v.repeat,
                v.category,
                v.score,
                v.red_flag,
                v.unscored,
                v.rationale,
            )
        )

    def on_judge_start(total: int, prior: list[Verdict]) -> None:
        _w(judge_events_mod.judge_start_event(time.time(), total))
        for v in prior:
            _verdict(v)

    def on_verdict(v: Verdict) -> None:
        _verdict(v)

    def write_masters(rows: list[dict[str, object]]) -> None:
        for row in rows:
            # row is dict[str, object]; narrow pct to float for mypy strict (no type: ignore)
            pct_raw = row["pct"]
            pct = float(pct_raw) if isinstance(pct_raw, (int, float)) else float(str(pct_raw))
            _w(
                judge_events_mod.master_event(
                    time.time(),
                    str(row["model"]),
                    str(row["variant"]),
                    pct,
                    bool(row["safety_passed"]),
                    str(row["safety_reason"]),
                    str(row["rubric_level"]),
                )
            )

    def judge_done(total: int, scored: int) -> None:
        try:
            _w(judge_events_mod.judge_done_event(time.time(), total, scored))
        finally:
            fh.close()  # close even if the final write fails (e.g. disk full)

    return on_judge_start, on_verdict, write_masters, judge_done


def _master_rows(
    pk: Pack,
    responses: list[EvalResponse],
    verdicts: list[Verdict],
    reports: list[ModelReport],
) -> list[dict[str, object]]:
    return scorecard_mod.master_rows(pk, responses, verdicts, reports)


def _run_event_writers(
    events_path: Path,
) -> tuple[
    Callable[[int], None],
    Callable[[int, Cell], None],
    Callable[[int, RunRecord], None],
    Callable[[list[RunRecord]], None],
]:
    """Closures that translate run_benchmark's callbacks into events.jsonl lines."""
    fh = events_path.open("a", encoding="utf-8")

    def _w(event: dict[str, object]) -> None:
        fh.write(events_mod.dumps(event) + "\n")
        fh.flush()

    def _variant(ctx: int) -> str:
        return "native" if ctx == 0 else f"ctx{ctx}"

    def on_run_start(total: int) -> None:
        _w(events_mod.run_start_event(time.time(), total))

    def on_cell_start(i: int, cell: Cell) -> None:
        _w(
            events_mod.cell_start_event(
                time.time(),
                i,
                cell.model.id,
                _variant(cell.target_ctx),
                cell.scenario,
                cell.scenario,
                0,
            )
        )

    def on_cell_done(i: int, rec: RunRecord) -> None:
        _w(
            events_mod.cell_done_event(
                time.time(),
                i,
                rec.model,
                _variant(rec.target_ctx),
                rec.scenario,
                0,
                rec.ok,
                rec.ttft_s,
                rec.e2e_s,
                rec.decode_tps,
                rec.completion_tokens,
                False,
                rec.error,
            )
        )

    def run_done(records: list[RunRecord]) -> None:
        try:
            _w(
                events_mod.run_done_event(
                    time.time(), len(records), sum(1 for r in records if r.ok)
                )
            )
        finally:
            fh.close()  # close even if the final write fails (e.g. disk full)

    return on_run_start, on_cell_start, on_cell_done, run_done


def _finalize_run_sentinel(run_dir: Path, *, ok: bool) -> None:
    """Best-effort: let a GUI-spawned run record its own terminal sentinel state.

    The GUI server spawns this CLI but never wait()s it, so a finished child lingers as a
    zombie that a naive liveness probe reads as 'running'. Writing the terminal state here
    makes the clean-finish path PID-independent. No-op outside the GUI control-plane (no
    run.json). The import is lazy + guarded so the harness core never hard-depends on the
    optional gui package and a broken gui never crashes a plain CLI run."""
    try:
        from touchstone.gui.control import finalize_sentinel

        finalize_sentinel(run_dir, ok=ok)
    except Exception:
        pass


def _finalize_run_report(records: list[RunRecord], run_dir: Path) -> None:
    """Write report.md + raw.csv for a finished benchmark run and print the summary."""
    md_path, raw_path = report.write_report(
        records, run_dir, date_str=_today(), host=hostinfo.summary()
    )
    ok = sum(1 for r in records if r.ok and not r.warmup and not r.is_cold_start)
    console.print(
        f"[green]✓[/] {len(records)} Requests ({ok} gewertet) · [bold]{md_path}[/] · {raw_path}"
    )


def _finalize_eval_bundle(
    run_dir: Path,
    pack: Path,
    cfg: Config,
    pk: Pack,
    responses: list[EvalResponse],
    client: OpenAIStreamClient,
) -> None:
    """Write manifest + scorecard + result.json for a finished eval and print the summary line."""
    from touchstone.result_schema import build_result_doc

    host = hostinfo.summary()
    _write_bundle_manifest(run_dir, pack, cfg, host)
    md = scorecard_mod.render_scorecard_md(pk, responses, [], [], host=host, date_str=_today())
    (run_dir / "scorecard.md").write_text(md, encoding="utf-8")
    build_meta = client.probe_build_metadata()
    doc = build_result_doc(
        pk,
        responses,
        [],
        [],
        host={**host, "date": _today(), "engine": cfg.engine or client.engine},
        build_meta=build_meta,
        judge=None,
        sampling={"seed": pk.sampling.seed, "temperature": pk.sampling.temperature},
    )
    (run_dir / "result.json").write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    errors = sum(1 for r in responses if not r.ok)
    console.print(
        f"[green]✓[/] {len(responses)} Antworten ({errors} Fehler) · "
        f"[bold]{run_dir / 'scorecard.md'}[/] (Tech-Specs gefüllt, Qualität offen) · "
        f"bewerten: [cyan]touchstone judge --bundle {run_dir} --judge-config judge.yaml[/]"
    )


@app.command(name="eval")
def eval_cmd(
    pack: Path = typer.Option(..., "--pack", exists=True, help="use-case pack YAML"),
    config: Path = typer.Option(..., "--config", "-c", exists=True, help="endpoint/models YAML"),
    out: Path | None = typer.Option(None, "--out", help="override output_dir"),
    resume: Path | None = typer.Option(
        None, "--resume", exists=True, help="continue an existing bundle dir (skip done cells)"
    ),
    run_dir_opt: Path | None = typer.Option(
        None, "--run-dir", help="use this exact run dir (GUI control-plane); overrides --out"
    ),
    web: bool = typer.Option(False, "--web", help="live browser monitor for this run"),
    port: int = typer.Option(0, "--port", help="monitor port (0 = auto)"),
    no_open: bool = typer.Option(False, "--no-open", help="don't auto-open the browser"),
    emit_events: bool = typer.Option(
        False, "--emit-events", help="write events.jsonl without spawning the monitor (GUI)"
    ),
    models_json: str = typer.Option(
        "",
        "--models-json",
        help="JSON list[ModelSpec]; replaces config.models for this run (GUI picker)",
    ),
    strict_preflight: bool = typer.Option(
        False,
        "--strict-preflight",
        help="abort before the matrix if a model emits no visible content",
    ),
) -> None:
    """Run a use-case pack through the models: capture answers + perf, write the bundle."""
    cfg = load_config(config)
    # Resume reruns the bundle's fixed cells — the model override never applies (M7; matches
    # the GUI route, which also gates the override on a non-resume start).
    if resume is None:
        try:
            cfg = apply_models_override(cfg, models_json)
        except ValueError as e:
            console.print(f"[red]--models-json:[/] {e}")
            raise typer.Exit(1) from None
    else:
        # Resume continues the BUNDLE's models (incl. extra_body), not the config's — a bundle
        # started with --models-json would otherwise resume with a different model.
        manifest_path = resume / "bundle.json"
        if manifest_path.exists():
            man = json.loads(manifest_path.read_text(encoding="utf-8"))
            cfg = apply_models_override(cfg, json.dumps(man.get("models") or []))
    pk = load_pack(pack)
    if resume is not None:
        run_dir = resume
        console.print(f"[bold]touchstone eval[/] [{pk.id}] → [cyan]{run_dir}[/] [dim](resume)[/]")
    elif run_dir_opt is not None:
        run_dir = run_dir_opt
        console.print(f"[bold]touchstone eval[/] [{pk.id}] → [cyan]{run_dir}[/]")
    else:
        base_out = out or cfg.output_path()
        run_dir = base_out / f"{_timestamp()}_eval_{pk.id}"
        console.print(f"[bold]touchstone eval[/] [{pk.id}] → [cyan]{run_dir}[/]")

    client = _make_client(cfg)

    def _print_preflight(results: list[PreflightResult]) -> None:
        bad = [r for r in results if r.status != "ok"]
        for r in bad:
            console.print(f"[yellow]⚠ Pre-Flight[/] {r.model}: {r.status} — {r.detail}")
        if not bad:
            console.print("[green]✓ Pre-Flight[/] alle Modelle liefern sichtbaren Content")

    # Wrap the whole run so a GUI-spawned eval records its terminal sentinel state itself
    # (finished/failed) — the overview must not depend on reaping the zombie child.
    try:
        emit = web or emit_events
        if not emit:
            responses = run_eval(
                cfg,
                pk,
                client,
                run_dir=run_dir,
                resume=resume is not None,
                on_preflight=_print_preflight,
                strict_preflight=strict_preflight,
            )
            _finalize_eval_bundle(run_dir, pack, cfg, pk, responses, client)
        else:
            run_dir.mkdir(parents=True, exist_ok=True)  # events.jsonl opened before run_eval
            monitor_cm = (
                _live_monitor(run_dir, port, no_open)
                if web
                else contextlib.nullcontext((None, None))
            )
            with monitor_cm as (monitor, url):
                # --web keeps append (resume); GUI --emit-events truncates (fresh stream).
                (on_run_start, on_cell_start, on_cell_done, on_preflight_evt, run_done) = (
                    _eval_event_writers(run_dir / "events.jsonl", append=web)
                )

                def _on_preflight(results: list[PreflightResult]) -> None:
                    _print_preflight(results)
                    on_preflight_evt(results)

                responses = []
                try:
                    responses = run_eval(
                        cfg,
                        pk,
                        client,
                        run_dir=run_dir,
                        resume=resume is not None,
                        on_run_start=on_run_start,
                        on_cell_start=on_cell_start,
                        on_cell_done=on_cell_done,
                        on_preflight=_on_preflight,
                        strict_preflight=strict_preflight,
                    )
                finally:
                    run_done(responses)
                _finalize_eval_bundle(run_dir, pack, cfg, pk, responses, client)
                if web and monitor is not None:
                    _hold_monitor(monitor, url)
    except BaseException:
        _finalize_run_sentinel(run_dir, ok=False)
        raise
    else:
        _finalize_run_sentinel(run_dir, ok=True)


def _judge_and_persist(
    backend: JudgeBackend,
    responses: list[EvalResponse],
    pk: Pack,
    prior: list[Verdict],
    jpath: Path,
    *,
    max_consecutive_failures: int,
    on_verdict: Callable[[Verdict], None] | None = None,
) -> tuple[list[Verdict], list[ModelReport]]:
    """Judge fresh responses, persist judgements.jsonl (append-stream then clean rewrite),
    return (verdicts, reports). ``on_verdict`` (web) is called in addition to the append.
    ``judge_error`` cells are never persisted (a resume re-judges them)."""
    with jpath.open("a", encoding="utf-8") as jh:

        def _append(v: Verdict) -> None:
            jh.write(json.dumps(v.as_dict(), ensure_ascii=False) + "\n")
            jh.flush()
            if on_verdict is not None:
                on_verdict(v)

        verdicts, reports = judge_bundle(
            backend,
            responses,
            pk,
            prior_verdicts=prior,
            on_verdict=_append,
            max_consecutive_failures=max_consecutive_failures,
        )
    with jpath.open("w", encoding="utf-8") as jh:  # clean rewrite: prior + new, deduped
        for v in verdicts:
            if v.judge_error:  # never persist error cells → a resume re-judges them
                continue
            jh.write(json.dumps(v.as_dict(), ensure_ascii=False) + "\n")
    return verdicts, reports


def _render_judge_scorecard(
    bundle: Path,
    pk: Pack,
    responses: list[EvalResponse],
    verdicts: list[Verdict],
    reports: list[ModelReport],
    host: dict[str, str],
) -> None:
    md = scorecard_mod.render_scorecard_md(
        pk, responses, verdicts, reports, host=host, date_str=_today()
    )
    (bundle / "scorecard.md").write_text(md, encoding="utf-8")
    rows = scorecard_mod.scores_csv_rows(pk, responses, verdicts, reports, host=host)
    if rows:
        with (bundle / "scores.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    write_reports_jsonl(bundle / "reports.jsonl", reports)


def _write_result_json_after_judge(
    bundle: Path,
    pk: Pack,
    responses: list[EvalResponse],
    verdicts: list[Verdict],
    reports: list[ModelReport],
    host: dict[str, str],
    jc: JudgeConfig,
) -> None:
    """Rewrite result.json after judging, preserving eval provenance from the prior file."""
    from touchstone.client import BuildMetadata
    from touchstone.result_schema import build_result_doc

    rj = bundle / "result.json"
    prior = json.loads(rj.read_text(encoding="utf-8")) if rj.exists() else None
    if prior:
        bm: BuildMetadata | None = BuildMetadata(
            engine_version=prior["provenance"].get("engine_version"),
            runtime=prior["provenance"].get("runtime"),
            quant_by_model={c["model"]: c["quant"] for c in prior["cells"] if c.get("quant")},
        )
    else:
        bm = None
    judge_info = {
        "model": jc.model,
        "version": None,
        "temperature": jc.temperature,
        "seed": getattr(jc, "seed", None),
    }
    # Enrich host with date (required by Provenance) and engine from prior if available.
    enriched_host: dict[str, str] = {**host, "date": _today()}
    if prior:
        prov = prior.get("provenance", {})
        if prov.get("engine") and "engine" not in host:
            enriched_host["engine"] = prov["engine"]
    doc = build_result_doc(
        pk,
        responses,
        verdicts,
        reports,
        host=enriched_host,
        build_meta=bm,
        judge=judge_info,
        sampling={"seed": pk.sampling.seed, "temperature": pk.sampling.temperature},
    )
    rj.write_text(doc.model_dump_json(indent=2), encoding="utf-8")


@app.command()
def judge(
    bundle: Path = typer.Option(..., "--bundle", exists=True, help="an eval bundle dir"),
    judge_config: Path | None = typer.Option(
        None, "--judge-config", help="judge endpoint YAML (omit → leave unscored)"
    ),
    judge_model: str = typer.Option(
        "", "--judge-model", help="override the judge model id from the config (GUI picker)"
    ),
    web: bool = typer.Option(False, "--web", help="live browser monitor for this judging run"),
    port: int = typer.Option(0, "--port", help="monitor port (0 = auto)"),
    no_open: bool = typer.Option(False, "--no-open", help="don't auto-open the browser"),
    emit_events: bool = typer.Option(
        False, "--emit-events", help="write judge_events.jsonl without spawning the monitor (GUI)"
    ),
) -> None:
    """Score an eval bundle with an LLM judge and fill the scorecard."""
    manifest = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
    pk = load_pack(manifest["pack_path"])
    responses = load_responses_jsonl(bundle / "responses.jsonl")
    host = manifest.get("host") or hostinfo.summary()

    if judge_config is None:
        console.print("[yellow]Kein --judge-config angegeben — Scorecard bleibt unbewertet.[/]")
        raise typer.Exit(code=1)

    jc = load_judge_config(judge_config)
    if judge_model.strip():
        jc = jc.model_copy(update={"model": judge_model.strip()})
    backend = OpenAIJudgeBackend(
        jc.endpoint.base_url,
        jc.endpoint.api_key,
        jc.model,
        jc.temperature,
        timeout=jc.call_timeout_s,
        max_tokens=jc.max_tokens,
        suppress_thinking=jc.suppress_thinking,
    )
    prior = load_judgements_jsonl(bundle / "judgements.jsonl")
    if prior:
        console.print(f"[dim]resume: {len(prior)} bereits bewertet — überspringe sie.[/]")
    console.print(f"[bold]touchstone judge[/] [{pk.id}] · judge: {jc.model}")
    # Persist which judge produced the scores so the report / Base can show it.
    manifest["judge"] = {
        "model": jc.model,
        "endpoint": jc.endpoint.base_url,
        "temperature": jc.temperature,
    }
    (bundle / "bundle.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    jpath = bundle / "judgements.jsonl"
    # Wrap so a GUI-spawned judge records its terminal sentinel state itself — the reported
    # bug was a dead judge process leaving the card pinned to 'Judge läuft' forever.
    try:
        emit = web or emit_events
        if not emit:
            verdicts, reports = _judge_and_persist(
                backend,
                responses,
                pk,
                prior,
                jpath,
                max_consecutive_failures=jc.max_consecutive_failures,
            )
            _render_judge_scorecard(bundle, pk, responses, verdicts, reports, host)
            _write_result_json_after_judge(bundle, pk, responses, verdicts, reports, host, jc)
            scored = sum(1 for v in verdicts if not v.unscored)
            console.print(
                f"[green]✓[/] {scored}/{len(verdicts)} bewertet · "
                f"[bold]{bundle / 'scorecard.md'}[/]"
            )
        else:
            monitor_cm = (
                _live_monitor(bundle, port, no_open, events_name="judge_events.jsonl", view="judge")
                if web
                else contextlib.nullcontext((None, None))
            )
            with monitor_cm as (monitor, url):
                on_judge_start, on_verdict, write_masters, judge_done = _judge_event_writers(
                    bundle / "judge_events.jsonl"
                )
                emit_verdicts: list[Verdict] = []
                emit_reports: list[ModelReport] = []
                try:
                    on_judge_start(len(responses), prior)
                    emit_verdicts, emit_reports = _judge_and_persist(
                        backend,
                        responses,
                        pk,
                        prior,
                        jpath,
                        on_verdict=on_verdict,
                        max_consecutive_failures=jc.max_consecutive_failures,
                    )
                    write_masters(_master_rows(pk, responses, emit_verdicts, emit_reports))
                finally:
                    judge_done(len(emit_verdicts), sum(1 for v in emit_verdicts if not v.unscored))
                _render_judge_scorecard(bundle, pk, responses, emit_verdicts, emit_reports, host)
                _write_result_json_after_judge(
                    bundle, pk, responses, emit_verdicts, emit_reports, host, jc
                )
                scored = sum(1 for v in emit_verdicts if not v.unscored)
                console.print(
                    f"[green]✓[/] {scored}/{len(emit_verdicts)} bewertet · "
                    f"[bold]{bundle / 'scorecard.md'}[/]"
                )
                if web and monitor is not None:
                    _hold_monitor(monitor, url)
    except JudgeAborted as e:
        _finalize_run_sentinel(bundle, ok=False)
        console.print(f"[red]Judge abgebrochen:[/] {e}")
        raise typer.Exit(code=1) from None
    except BaseException:
        _finalize_run_sentinel(bundle, ok=False)
        raise
    else:
        _finalize_run_sentinel(bundle, ok=True)


judge_meta_app = typer.Typer(add_completion=False, help="Meta-evaluate the local judge's quality.")
app.add_typer(judge_meta_app, name="judge-meta")


@judge_meta_app.command("export")
def judge_meta_export(
    bundle: Path = typer.Argument(..., exists=True, help="a judged eval bundle (run dir)"),
) -> None:
    """Write judge_meta_request.md + an empty judge_meta_response.yaml for an external cloud AI."""
    from touchstone.gui import bundles
    from touchstone.gui.judge_meta import empty_response_template, render_request_md

    detail = bundles.bundle_detail(bundle)
    if detail is None or not detail.get("reports"):
        typer.echo(
            "Dieses Bundle hat noch keine Judge-Bewertung (reports.jsonl). "
            "Bitte erst `touchstone judge` laufen lassen.",
            err=True,
        )
        raise typer.Exit(code=1)
    cells = sorted({(r.model, r.variant) for r in detail["reports"]})
    (bundle / "judge_meta_request.md").write_text(render_request_md(detail), encoding="utf-8")
    (bundle / "judge_meta_response.yaml").write_text(
        empty_response_template(detail["pack"], cells), encoding="utf-8"
    )
    typer.echo(
        f"Geschrieben: {bundle / 'judge_meta_request.md'} + judge_meta_response.yaml\n"
        "→ Request durch eine Cloud-KI jagen, judge_meta_response.yaml ausfüllen, dann "
        "`touchstone judge-meta ingest` laufen lassen."
    )


@judge_meta_app.command("ingest")
def judge_meta_ingest(
    bundle: Path = typer.Argument(..., exists=True, help="the bundle with a filled response"),
    response: Path | None = typer.Option(None, "--response", help="path to the filled YAML"),
) -> None:
    """Compute agreement + rationale-quality rubric → judge_quality.md."""
    from touchstone.gui import bundles
    from touchstone.gui.judge_meta import (
        aggregate_rubric,
        compute_agreement,
        parse_meta_response,
        render_judge_quality_md,
    )

    rpath = response or (bundle / "judge_meta_response.yaml")
    if not rpath.exists():
        typer.echo(
            f"Keine Response-Datei: {rpath} (erst `judge-meta export` + ausfüllen).", err=True
        )
        raise typer.Exit(code=1)
    detail = bundles.bundle_detail(bundle)
    if detail is None or not detail.get("reports"):
        typer.echo("Bundle ohne Judge-Bewertung — `touchstone judge` zuerst.", err=True)
        raise typer.Exit(code=1)
    try:
        meta = parse_meta_response(rpath.read_text(encoding="utf-8"))
    except Exception as exc:  # surface a clean message, never a stacktrace
        typer.echo(f"judge_meta_response.yaml ungültig: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    agreement = compute_agreement(detail["pack"], detail["reports"], detail["master_rows"], meta)
    rubric = aggregate_rubric(detail["reports"], meta)
    md = render_judge_quality_md(detail, agreement, rubric, meta)
    (bundle / "judge_quality.md").write_text(md, encoding="utf-8")
    typer.echo(f"Geschrieben: {bundle / 'judge_quality.md'}")


@app.command()
def gui(
    runs: Path = typer.Option(
        Path("./runs"), "--runs", help="runs dir to read/write bundles under"
    ),
    port: int = typer.Option(0, "--port", help="server port (0 = auto)"),
    no_open: bool = typer.Option(False, "--no-open", help="don't auto-open the browser"),
) -> None:
    """Launch the local web control-center (requires the [gui] extra)."""
    try:
        from touchstone.gui.app import serve
    except ImportError:
        console.print(
            "[red]GUI-Abhängigkeiten fehlen.[/] Installiere sie mit "
            r"[cyan]pip install -e '.\[gui]'[/] (oder [cyan]uv sync --extra gui[/])."
        )
        raise typer.Exit(code=1) from None
    serve(runs_dir=runs, port=port, open_browser=not no_open)


@app.command(name="queue")
def queue_cmd(
    queue_file: Path = typer.Option(..., "--queue", "-q", exists=True, help="queue.yaml"),
    check: bool = typer.Option(
        False,
        "--check",
        help="verify the model-switch chain only (reset+settle+1 request), no matrix",
    ),
    resume: Path | None = typer.Option(
        None, "--resume", help="continue an existing runs/<ts>_queue dir (skip completed entries)"
    ),
) -> None:
    """Run several models overnight, sequentially: per entry reset→settle→eval→[judge]."""
    spec = rq.load_queue(queue_file)
    if check:
        check_dir = Path("./runs") / f"{_timestamp()}_check"
        console.print("[bold]touchstone queue --check[/] — Modell-Wechsel-Probe")
        rq.run_check(spec, emit=console.print, check_dir=check_dir)
        console.print(f"[green]✓[/] check.md: [cyan]{check_dir / 'check.md'}[/]")
        return
    output_dir = Path("./runs")
    if resume is not None:
        queue_dir = resume
        prior = rq.load_prior_results(queue_dir)
        ts = queue_dir.name.replace("_queue", "")
        console.print(f"[bold]touchstone queue[/] (resume) → [cyan]{queue_dir}[/]")
    else:
        ts = _timestamp()
        queue_dir = output_dir / f"{ts}_queue"
        prior = []
        console.print(
            f"[bold]touchstone queue[/] → [cyan]{queue_dir}[/] ({len(spec.entries)} Einträge)"
        )
    results = rq.run_queue(
        spec,
        queue_dir=queue_dir,
        output_dir=output_dir,
        python=sys.executable,
        run_step=rq.make_run_step(clock=time.monotonic),
        reset_run=rq.run_reset,
        ram_poll=rq.default_ram_poll,
        sleep=time.sleep,
        clock=time.monotonic,
        ts=ts,
        started_iso=datetime.now().isoformat(timespec="seconds"),
        prior=prior,
        log=lambda m: console.print(f"[yellow]{m}[/]"),
    )
    ok = sum(1 for r in results if r.eval_status == "ok")
    console.print(
        f"[green]Queue fertig[/] — {ok}/{len(results)} eval ok · "
        f"Summary: [cyan]{queue_dir / 'summary.md'}[/]"
    )


@app.command(name="tools")
def tools_cmd(
    pack: Path = typer.Option(..., "--pack", exists=True, help="tools pack YAML"),
    config: Path = typer.Option(..., "--config", "-c", exists=True, help="endpoint/models YAML"),
    resume: Path | None = typer.Option(
        None, "--resume", exists=True, help="continue an existing tools bundle (skip done cells)"
    ),
    run_dir_opt: Path | None = typer.Option(None, "--run-dir", help="use this exact run dir"),
    models_json: str = typer.Option(
        "", "--models-json", help="JSON list[ModelSpec]; replaces config.models for this run"
    ),
    items: str = typer.Option("", "--items", help="comma-separated item ids (smoke subset)"),
) -> None:
    """Deterministic tool-calling/code pack: tool choice, JSON args vs schema, code that runs."""
    from touchstone import toolbench as tb

    cfg = load_config(config)
    if resume is None:
        try:
            cfg = apply_models_override(cfg, models_json)
        except ValueError as e:
            console.print(f"[red]--models-json:[/] {e}")
            raise typer.Exit(1) from None
    pk = tb.load_tools_pack(pack)
    fingerprint = tb.pack_fingerprint(pack, pk)
    wanted = [i.strip() for i in items.split(",") if i.strip()]
    if wanted:
        unknown = sorted(set(wanted) - {i.id for i in pk.items})
        if unknown:
            console.print(f"[red]--items: unbekannt:[/] {unknown}")
            raise typer.Exit(1)
        pk = pk.model_copy(update={"items": [i for i in pk.items if i.id in wanted]})
    node = tb.node_path()
    if tb.needs_node(pk) and node is None:
        # Without node the JS checks would be "not measured" and those items could pass on the
        # remaining checks alone — unfair between two runs on differently set-up shells.
        console.print("[red]Pack braucht node (JS-Checks), aber node ist nicht im PATH.[/]")
        raise typer.Exit(1)
    run_dir = resume or run_dir_opt or cfg.output_path() / f"{_timestamp()}_tools_{pk.id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "bundle.json"
    if resume is not None:
        if not manifest_path.exists():
            console.print(f"[red]--resume: kein bundle.json in {run_dir}[/]")
            raise typer.Exit(1)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("pack_sha256") != fingerprint or manifest.get("items") != (wanted or None):
            console.print(
                "[red]--resume: Pack (Inhalt/Kontext) oder --items weichen vom ursprünglichen "
                "Lauf ab — ein Mischlauf wäre nicht vergleichbar.[/]"
            )
            raise typer.Exit(1)
        models = [
            (m["id"], m.get("quant", ""), m.get("extra_body") or {}) for m in manifest["models"]
        ]
    else:
        models = [(m.id, m.quant, dict(m.extra_body)) for m in cfg.models]
        manifest = {
            "kind": "tools",
            "pack_id": pk.id,
            "pack_version": pk.version,
            "pack_path": str(Path(pack).resolve()),
            "pack_sha256": fingerprint,
            "items": wanted or None,
            "models": [
                {
                    "id": i,
                    "quant": q,
                    "extra_body": e,
                    "reasoning_effort": e.get("reasoning_effort"),
                }
                for i, q, e in models
            ],
            "endpoint": cfg.endpoint.base_url,
            "host": hostinfo.summary(),
            "node": node,
            "sampling": pk.sampling.model_dump(),
            "max_tokens": pk.max_tokens,
            "date": _today(),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    console.print(f"[bold]touchstone tools[/] [{pk.id}] → [cyan]{run_dir}[/]")
    # max_retries=0: a silent SDK retry would re-send a long-context request and inflate e2e_s;
    # a transport error is recorded instead and re-run on resume.
    engine = cfg.engine or resolve_engine(cfg.endpoint.base_url)
    client = OpenAIStreamClient(
        cfg.endpoint.base_url, cfg.endpoint.api_key, engine=engine,
        engine_version=cfg.engine_version or "unknown", max_retries=0,
    )  # fmt: skip
    for mid, _q, eb in models:
        err = tb.preflight(client.stream_tools, mid, eb, pk)
        if err:
            # e.g. an invalid reasoning_effort → the chat template raises; never fall back silently
            console.print(f"[red]✗ Pre-Flight {mid} {eb or ''}:[/] {err}")
            manifest["preflight_error"] = err
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
            raise typer.Exit(1)
    console.print("[green]✓ Pre-Flight[/] Endpoint nimmt die Modell-Parameter an")
    aborted = ""
    try:
        tb.run_tools(
            pk, models, client.stream_tools, run_dir, resume=resume is not None, log=console.print
        )
    except tb.ToolsAborted as e:
        aborted = str(e)
        console.print(f"[red]✗ abgebrochen:[/] {aborted}")
    rows = tb.load_tool_responses(run_dir / "responses.jsonl")
    # Evidence of what was actually loaded (LM Studio: quantization + context) — best-effort.
    build = client.probe_build_metadata()
    manifest["loaded_at_end"] = build.loaded
    manifest["aborted"] = aborted or None
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    tb.write_results_csv(rows, run_dir / "results.csv")
    meta = {
        "Endpoint": cfg.endpoint.base_url,
        "Datum": _today(),
        "Geladen laut Server am Ende": build.loaded or "n. v.",
        "Sampling": pk.sampling.model_dump(),
        "max_tokens": pk.max_tokens,
        "node": node or "n. v.",
        "Abbruch": aborted or "nein",
    }
    (run_dir / "report.md").write_text(tb.render_report_md(pk, rows, meta), encoding="utf-8")
    console.print(
        f"[green]✓[/] {sum(r.passed for r in rows)}/{len(rows)} Items bestanden → "
        f"{run_dir / 'report.md'}"
    )
    if aborted:
        raise typer.Exit(1)


@app.command(name="tools-compare")
def tools_compare_cmd(
    bundle_a: Path = typer.Argument(..., exists=True, help="tools bundle A"),
    bundle_b: Path = typer.Argument(..., exists=True, help="tools bundle B"),
    out: Path | None = typer.Option(None, "--out", help="write the comparison markdown here"),
) -> None:
    """Paired A/B over the same tools pack: per-item pass, discordant pairs, exact McNemar."""
    from touchstone import toolbench as tb

    a = tb.load_tool_responses(bundle_a / "responses.jsonl")
    b = tb.load_tool_responses(bundle_b / "responses.jsonl")
    if not a or not b:
        console.print("[red]leeres Bundle[/]")
        raise typer.Exit(1)
    try:
        cmp = tb.compare_bundles(a, b)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    mans = []
    for bdir in (bundle_a, bundle_b):
        mf = bdir / "bundle.json"
        mans.append(json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {})
    shas = [m.get("pack_sha256") for m in mans]
    if None in shas or shas[0] != shas[1]:
        # pack_version alone is not enough: check semantics can change without a version bump.
        console.print(f"[red]Pack-Hash fehlt oder weicht ab (A={shas[0]}, B={shas[1]}) — "
                      "die Läufe sind nicht mit identischen Checks gemessen.[/]")  # fmt: skip
        raise typer.Exit(1)
    notes = []
    for label, man in (("A", mans[0]), ("B", mans[1])):
        ctx = [m.get("loaded_context_length") for m in man.get("loaded_at_end") or []]
        notes.append(f"- {label}: geladen laut Server {man.get('loaded_at_end') or 'n. v.'}")
        if any(isinstance(c, int) and c < 90_000 for c in ctx):
            notes.append(f"- ⚠ {label}: Kontext < 90k — Langkontext-Items (L*) evtl. abgeschnitten")
        if man.get("aborted"):
            notes.append(f"- ⚠ {label}: Lauf abgebrochen: {man['aborted']}")
    md = tb.render_compare_md(cmp) + "\n## Belege\n\n" + "\n".join(notes) + "\n"
    if out is not None:
        out.write_text(md, encoding="utf-8")
    console.print(md)


if __name__ == "__main__":  # pragma: no cover
    app()
