"""ramcheck CLI — run · embed · report.

ramcheck run    --config config.m1.yaml    # M1: gegen LM Studio
ramcheck run    --config config.m5.yaml    # M5: gegen mlx_lm.server / mlx-openai-server
ramcheck embed  --config config.m5.yaml    # Embedding-Durchsatz separat
ramcheck report --runs ./runs              # report.md (re)generieren
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from ramcheck import hostinfo, report
from ramcheck import scorecard as scorecard_mod
from ramcheck.client import OpenAIStreamClient
from ramcheck.config import Config, load_config
from ramcheck.embed import render_embed_md, run_embed
from ramcheck.judge import (
    OpenAIJudgeBackend,
    judge_bundle,
    load_judge_config,
    load_judgements_jsonl,
)
from ramcheck.pack import load_pack
from ramcheck.qualrun import load_responses_jsonl, run_eval
from ramcheck.report import load_raw_csv
from ramcheck.results import Verdict
from ramcheck.runner import resolve_engine, run_benchmark

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
    records = run_benchmark(cfg, client, run_dir=run_dir, settle_s=settle)

    md_path, raw_path = report.write_report(
        records, run_dir, date_str=_today(), host=hostinfo.summary()
    )
    ok = sum(1 for r in records if r.ok and not r.warmup and not r.is_cold_start)
    console.print(
        f"[green]✓[/] {len(records)} Requests ({ok} gewertet) · [bold]{md_path}[/] · {raw_path}"
    )


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


@app.command(name="eval")
def eval_cmd(
    pack: Path = typer.Option(..., "--pack", exists=True, help="use-case pack YAML"),
    config: Path = typer.Option(..., "--config", "-c", exists=True, help="endpoint/models YAML"),
    out: Path | None = typer.Option(None, "--out", help="override output_dir"),
    resume: Path | None = typer.Option(
        None, "--resume", exists=True, help="continue an existing bundle dir (skip done cells)"
    ),
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
    responses = run_eval(cfg, pk, client, run_dir=run_dir, resume=resume is not None)
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


@app.command()
def judge(
    bundle: Path = typer.Option(..., "--bundle", exists=True, help="an eval bundle dir"),
    judge_config: Path | None = typer.Option(
        None, "--judge-config", help="judge endpoint YAML (omit → leave unscored)"
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
    backend = OpenAIJudgeBackend(
        jc.endpoint.base_url, jc.endpoint.api_key, jc.model, jc.temperature
    )
    prior = load_judgements_jsonl(bundle / "judgements.jsonl")
    if prior:
        console.print(f"[dim]resume: {len(prior)} bereits bewertet — überspringe sie.[/]")
    console.print(f"[bold]ramcheck judge[/] [{pk.id}] · judge: {jc.model}")

    jpath = bundle / "judgements.jsonl"
    with jpath.open("a", encoding="utf-8") as jh:

        def _append(v: Verdict) -> None:  # persist each verdict immediately (resumable)
            jh.write(json.dumps(v.as_dict(), ensure_ascii=False) + "\n")
            jh.flush()

        verdicts, reports = judge_bundle(
            backend, responses, pk, prior_verdicts=prior, on_verdict=_append
        )
    with jpath.open("w", encoding="utf-8") as jh:  # clean rewrite: prior + new, deduped
        for v in verdicts:
            jh.write(json.dumps(v.as_dict(), ensure_ascii=False) + "\n")

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
    scored = sum(1 for v in verdicts if not v.unscored)
    console.print(
        f"[green]✓[/] {scored}/{len(verdicts)} bewertet · [bold]{bundle / 'scorecard.md'}[/]"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
