# tests/test_cli_judge_meta.py
import json
from pathlib import Path

from typer.testing import CliRunner

from touchstone.cli import app
from touchstone.judge import write_reports_jsonl
from touchstone.pack import load_pack
from touchstone.results import ModelReport

PACK = "packs/ndassist.yaml"
runner = CliRunner()


def _judged_bundle(d: Path):
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    d.mkdir(parents=True)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "host": {"chip": "M5", "ram_gb": "64 GB"},
                "date": "2026-06-24",
                "judge": {"model": "qwen3-27b"},
            }
        ),
        encoding="utf-8",
    )
    base = {
        "pack_id": pk.id,
        "pack_version": 1,
        "machine": "t",
        "model": "m",
        "quant": "q4",
        "engine": "lm-studio",
        "engine_version": "0",
        "variant": "baseline",
        "category": "A",
        "prompt_id": first.id,
        "repeat": 0,
        "response_text": "A.",
        "content_empty": False,
        "ttft_s": 0.2,
        "decode_tps": 30.0,
        "prefill_tps": 90.0,
        "e2e_s": 1.5,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "is_cold_start": False,
        "power_source": "ac",
        "peak_rss_mb": 0.0,
        "sys_used_mb": 20000.0,
        "mem_pressure_max": "normal",
        "throttled": False,
        "ok": True,
        "error": "",
        "seed": 42,
        "t_start": 0.0,
        "t_end": 1.5,
        "reasoning_chars": 0,
    }
    (d / "responses.jsonl").write_text(json.dumps(base) + "\n", encoding="utf-8")
    hdr = "model,variant,metric_type,metric,weight,score"
    rows = [hdr] + [f"m,baseline,dimension,{dim.id},{dim.weight},4" for dim in pk.dimensions]
    (d / "scores.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    write_reports_jsonl(
        d / "reports.jsonl",
        [
            ModelReport(
                model="m",
                variant="baseline",
                dim_scores={dim.id: 4 for dim in pk.dimensions},
                dim_rationales={pk.dimensions[0].id: "gut"},
            )
        ],
    )
    return pk


def test_export_writes_request_and_template(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    res = runner.invoke(app, ["judge-meta", "export", str(d)])
    assert res.exit_code == 0, res.output
    assert (d / "judge_meta_request.md").exists()
    assert (d / "judge_meta_response.yaml").exists()


def test_export_refuses_unjudged_bundle(tmp_path):
    pk = load_pack(PACK)
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps({"pack_id": pk.id, "pack_path": PACK, "host": {}, "date": "x"}), encoding="utf-8"
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")  # no reports.jsonl → unjudged
    res = runner.invoke(app, ["judge-meta", "export", str(d)])
    assert res.exit_code != 0
    assert "judge" in res.output.lower()


def test_ingest_writes_judge_quality(tmp_path):
    d = tmp_path / "2026_eval_nd"
    pk = _judged_bundle(d)
    dims = "{" + ", ".join(f"{dim.id}: 4" for dim in pk.dimensions) + "}"
    crit = "\n".join(
        f"        {dim.id}: {{cites_evidence: true, names_improvement: true, justifies_level: true, catches_safety: true}}"
        for dim in pk.dimensions
    )
    (d / "judge_meta_response.yaml").write_text(
        "cells:\n  - model: m\n    variant: baseline\n"
        f"    fresh_scores: {{dimensions: {dims}, ko_fired: false, overall: Ja}}\n"
        f"    critique:\n      dimensions:\n{crit}\n      summary: 'ok'\n"
        "recommendations:\n  - 'Mehr Belege zitieren.'\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["judge-meta", "ingest", str(d)])
    assert res.exit_code == 0, res.output
    qa = (d / "judge_quality.md").read_text(encoding="utf-8")
    assert "## Headline" in qa and "Mehr Belege zitieren." in qa


def test_ingest_missing_response_errors(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    res = runner.invoke(app, ["judge-meta", "ingest", str(d)])
    assert res.exit_code != 0
