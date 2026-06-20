"""ramcheck CLI — run · embed · report.

ramcheck run    --config config.m1.yaml    # M1: gegen LM Studio
ramcheck run    --config config.m5.yaml    # M5: gegen mlx_lm.server / mlx-openai-server
ramcheck embed  --config config.m5.yaml    # Embedding-Durchsatz separat
ramcheck report --runs ./runs              # report.md (re)generieren
"""

from __future__ import annotations

import contextlib
import csv
import json
import time
import webbrowser
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from ramcheck import aggregate as aggregate_mod
from ramcheck import events as events_mod
from ramcheck import hostinfo, report
from ramcheck import judge_events as judge_events_mod
from ramcheck import scorecard as scorecard_mod
from ramcheck.client import OpenAIStreamClient
from ramcheck.config import Config, load_config
from ramcheck.embed import render_embed_md, run_embed
from ramcheck.judge import (
    JudgeBackend,
    OpenAIJudgeBackend,
    judge_bundle,
    load_judge_config,
    load_judgements_jsonl,
)
from ramcheck.models import RunRecord
from ramcheck.pack import Pack, load_pack
from ramcheck.qualrun import EvalCell, load_responses_jsonl, run_eval
from ramcheck.report import load_raw_csv
from ramcheck.results import EvalResponse, ModelReport, Verdict
from ramcheck.runner import Cell, _WebMonitorProcess, resolve_engine, run_benchmark

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
        f"[bold]ramcheck run[/] → [cyan]{run_dir}[/]  (engine: {cfg.engine or resolve_engine(cfg.endpoint.base_url)})"
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
    console.print(f"[bold]ramcheck embed[/] → [cyan]{run_dir}[/]  (model: {cfg.embed.model})")

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
        console.print(f"[red]Keine scores.csv unter {runs} — erst `ramcheck judge` ausführen.[/]")
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
    from ramcheck.pack import load_pack as _lp

    pk = _lp(pack_path)
    manifest = {
        "pack_id": pk.id,
        "pack_version": pk.version,
        "pack_path": str(Path(pack_path).resolve()),
        "machine": cfg.machine,
        "models": [{"id": m.id, "quant": m.quant} for m in cfg.models],
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
) -> tuple[
    Callable[[int], None],
    Callable[[int, EvalCell], None],
    Callable[[int, EvalResponse], None],
    Callable[[list[EvalResponse]], None],
]:
    """Closures that translate run_eval's callbacks into events.jsonl lines."""
    fh = events_path.open("a", encoding="utf-8")

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

    def run_done(responses: list[EvalResponse]) -> None:
        try:
            _w(
                events_mod.run_done_event(
                    time.time(), len(responses), sum(1 for r in responses if r.ok)
                )
            )
        finally:
            fh.close()  # close even if the final write fails (e.g. disk full)

    return on_run_start, on_cell_start, on_cell_done, run_done


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
                    str(row["recommendation"]),
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
    """Per-(model, variant) master summary, computed in the host process (J6): the monitor
    only displays it. Reuses scorecard's rules so the dashboard matches scorecard.md."""
    reports_by = {(r.model, r.variant): r for r in reports}
    rows: list[dict[str, object]] = []
    for model, variant in scorecard_mod.model_variant_groups(responses):
        rep = reports_by.get((model, variant))
        if not (rep and rep.dim_scores):
            continue
        _, _, pct = scorecard_mod.weighted_total(rep.dim_scores, pk)
        gv = [v for v in verdicts if (v.model, v.variant) == (model, variant)]
        passed, reason = scorecard_mod.passes_ko(
            rep.dim_scores, scorecard_mod.red_flagged_prompts(gv), pk
        )
        # reuse scorecard.recommendation so the dashboard verdict matches scorecard.md exactly
        rows.append(
            {
                "model": model,
                "variant": variant,
                "pct": pct,
                "safety_passed": passed,
                "safety_reason": reason,
                "recommendation": scorecard_mod.recommendation(passed, pct),
            }
        )
    return rows


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
    run_dir: Path, pack: Path, cfg: Config, pk: Pack, responses: list[EvalResponse]
) -> None:
    """Write manifest + scorecard for a finished eval and print the summary line."""
    host = hostinfo.summary()
    _write_bundle_manifest(run_dir, pack, cfg, host)
    md = scorecard_mod.render_scorecard_md(pk, responses, [], [], host=host, date_str=_today())
    (run_dir / "scorecard.md").write_text(md, encoding="utf-8")
    errors = sum(1 for r in responses if not r.ok)
    console.print(
        f"[green]✓[/] {len(responses)} Antworten ({errors} Fehler) · "
        f"[bold]{run_dir / 'scorecard.md'}[/] (Tech-Specs gefüllt, Qualität offen) · "
        f"bewerten: [cyan]ramcheck judge --bundle {run_dir} --judge-config judge.yaml[/]"
    )


@app.command(name="eval")
def eval_cmd(
    pack: Path = typer.Option(..., "--pack", exists=True, help="use-case pack YAML"),
    config: Path = typer.Option(..., "--config", "-c", exists=True, help="endpoint/models YAML"),
    out: Path | None = typer.Option(None, "--out", help="override output_dir"),
    resume: Path | None = typer.Option(
        None, "--resume", exists=True, help="continue an existing bundle dir (skip done cells)"
    ),
    web: bool = typer.Option(False, "--web", help="live browser monitor for this run"),
    port: int = typer.Option(0, "--port", help="monitor port (0 = auto)"),
    no_open: bool = typer.Option(False, "--no-open", help="don't auto-open the browser"),
) -> None:
    """Run a use-case pack through the models: capture answers + perf, write the bundle."""
    cfg = load_config(config)
    pk = load_pack(pack)
    if resume is not None:
        run_dir = resume
        console.print(f"[bold]ramcheck eval[/] [{pk.id}] → [cyan]{run_dir}[/] [dim](resume)[/]")
    else:
        base_out = out or cfg.output_path()
        run_dir = base_out / f"{_timestamp()}_eval_{pk.id}"
        console.print(f"[bold]ramcheck eval[/] [{pk.id}] → [cyan]{run_dir}[/]")

    client = _make_client(cfg)
    if not web:
        responses = run_eval(cfg, pk, client, run_dir=run_dir, resume=resume is not None)
        _finalize_eval_bundle(run_dir, pack, cfg, pk, responses)
        return

    run_dir.mkdir(parents=True, exist_ok=True)  # events.jsonl is opened before run_eval
    with _live_monitor(run_dir, port, no_open) as (monitor, url):
        on_run_start, on_cell_start, on_cell_done, run_done = _eval_event_writers(
            run_dir / "events.jsonl"
        )
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
            )
        finally:
            run_done(responses)  # final event so the dashboard shows "fertig"
        _finalize_eval_bundle(run_dir, pack, cfg, pk, responses)
        _hold_monitor(monitor, url)


def _judge_and_persist(
    backend: JudgeBackend,
    responses: list[EvalResponse],
    pk: Pack,
    prior: list[Verdict],
    jpath: Path,
    *,
    on_verdict: Callable[[Verdict], None] | None = None,
) -> tuple[list[Verdict], list[ModelReport]]:
    """Judge fresh responses, persist judgements.jsonl (append-stream then clean rewrite),
    return (verdicts, reports). ``on_verdict`` (web) is called in addition to the append."""
    with jpath.open("a", encoding="utf-8") as jh:

        def _append(v: Verdict) -> None:
            jh.write(json.dumps(v.as_dict(), ensure_ascii=False) + "\n")
            jh.flush()
            if on_verdict is not None:
                on_verdict(v)

        verdicts, reports = judge_bundle(
            backend, responses, pk, prior_verdicts=prior, on_verdict=_append
        )
    with jpath.open("w", encoding="utf-8") as jh:  # clean rewrite: prior + new, deduped
        for v in verdicts:
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


@app.command()
def judge(
    bundle: Path = typer.Option(..., "--bundle", exists=True, help="an eval bundle dir"),
    judge_config: Path | None = typer.Option(
        None, "--judge-config", help="judge endpoint YAML (omit → leave unscored)"
    ),
    web: bool = typer.Option(False, "--web", help="live browser monitor for this judging run"),
    port: int = typer.Option(0, "--port", help="monitor port (0 = auto)"),
    no_open: bool = typer.Option(False, "--no-open", help="don't auto-open the browser"),
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
    backend = OpenAIJudgeBackend(
        jc.endpoint.base_url, jc.endpoint.api_key, jc.model, jc.temperature
    )
    prior = load_judgements_jsonl(bundle / "judgements.jsonl")
    if prior:
        console.print(f"[dim]resume: {len(prior)} bereits bewertet — überspringe sie.[/]")
    console.print(f"[bold]ramcheck judge[/] [{pk.id}] · judge: {jc.model}")

    jpath = bundle / "judgements.jsonl"
    if not web:
        verdicts, reports = _judge_and_persist(backend, responses, pk, prior, jpath)
        _render_judge_scorecard(bundle, pk, responses, verdicts, reports, host)
        scored = sum(1 for v in verdicts if not v.unscored)
        console.print(
            f"[green]✓[/] {scored}/{len(verdicts)} bewertet · [bold]{bundle / 'scorecard.md'}[/]"
        )
        return

    with _live_monitor(bundle, port, no_open, events_name="judge_events.jsonl", view="judge") as (
        monitor,
        url,
    ):
        on_judge_start, on_verdict, write_masters, judge_done = _judge_event_writers(
            bundle / "judge_events.jsonl"
        )
        web_verdicts: list[Verdict] = []
        web_reports: list[ModelReport] = []
        try:
            on_judge_start(len(responses), prior)
            web_verdicts, web_reports = _judge_and_persist(
                backend, responses, pk, prior, jpath, on_verdict=on_verdict
            )
            write_masters(_master_rows(pk, responses, web_verdicts, web_reports))
        finally:
            judge_done(len(web_verdicts), sum(1 for v in web_verdicts if not v.unscored))
        _render_judge_scorecard(bundle, pk, responses, web_verdicts, web_reports, host)
        scored = sum(1 for v in web_verdicts if not v.unscored)
        console.print(
            f"[green]✓[/] {scored}/{len(web_verdicts)} bewertet · [bold]{bundle / 'scorecard.md'}[/]"
        )
        _hold_monitor(monitor, url)


if __name__ == "__main__":  # pragma: no cover
    app()
