import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.judge import write_reports_jsonl
from touchstone.pack import load_pack
from touchstone.results import ModelReport

PACK = "packs/ndassist.yaml"


class _L:
    def spawn(self, a):
        return 1

    def alive(self, p):
        return False

    def terminate(self, p):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_L())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _judged_bundle(d: Path):
    """A minimal judged bundle (bundle.json + responses + scores + reports.jsonl)."""
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


def _valid_response_yaml(pk) -> str:
    dims = "{" + ", ".join(f"{dim.id}: 4" for dim in pk.dimensions) + "}"
    crit = "\n".join(
        f"        {dim.id}: {{cites_evidence: true, names_improvement: true, "
        f"justifies_level: true, catches_safety: true}}"
        for dim in pk.dimensions
    )
    return (
        "cells:\n  - model: m\n    variant: baseline\n"
        f"    fresh_scores: {{dimensions: {dims}, ko_fired: false, overall: Ja}}\n"
        f"    critique:\n      dimensions:\n{crit}\n      summary: 'ok'\n"
        "recommendations:\n  - 'Mehr Belege zitieren.'\n"
    )


def test_export_request_streams_md_for_judged(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    r = _client(tmp_path).get(f"/export-judge-meta-request/{d.name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "judge_meta_request.md" in r.headers["content-disposition"]
    assert "Teil A" in r.text and "Teil B" in r.text


def test_export_request_refuses_unjudged(tmp_path):
    pk = load_pack(PACK)
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps({"pack_id": pk.id, "pack_path": PACK, "host": {}, "date": "x"}),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")  # no reports.jsonl → unjudged
    r = _client(tmp_path).get(f"/export-judge-meta-request/{d.name}")
    assert r.status_code == 400


def test_export_request_traversal_404(tmp_path):
    _judged_bundle(tmp_path / "2026_eval_nd")
    r = _client(tmp_path).get("/export-judge-meta-request/..%2f..%2fetc")
    assert r.status_code == 404


def test_export_template_streams_yaml_round_trips(tmp_path):
    from touchstone.gui import judge_meta

    d = tmp_path / "2026_eval_nd"
    _judged_bundle(d)
    r = _client(tmp_path).get(f"/export-judge-meta-template/{d.name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-yaml")
    assert "judge_meta_response.yaml" in r.headers["content-disposition"]
    parsed = judge_meta.parse_meta_response(r.text)  # must round-trip
    assert any(c.model == "m" and c.variant == "baseline" for c in parsed.cells)


def test_export_template_refuses_unjudged(tmp_path):
    pk = load_pack(PACK)
    d = tmp_path / "2026_eval_raw"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps({"pack_id": pk.id, "pack_path": PACK, "host": {}, "date": "x"}),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    r = _client(tmp_path).get(f"/export-judge-meta-template/{d.name}")
    assert r.status_code == 400
