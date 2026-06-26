# tests/test_gui_meta_report_routes.py
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.pack import load_pack

PACK = "packs/ndassist.yaml"
HOST = {"chip": "Apple M5 Pro", "ram_gb": "64.0 GB", "machine": "M5-64GB", "engine": "lm-studio"}


class _FakeLauncher:
    def spawn(self, argv):
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _write_bundle(d, *, model="m", variant="baseline"):
    pk = load_pack(PACK)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "models": [{"id": model, "quant": "q4"}],
                "host": HOST,
                "date": "2026-06-24",
                "seed": 42,
            }
        ),
        encoding="utf-8",
    )
    first = next(p for _, p in pk.all_prompts())
    base = dict(
        pack_id=pk.id,
        pack_version=1,
        machine="t",
        model=model,
        quant="q4",
        engine="lm-studio",
        engine_version="0",
        variant=variant,
        category="A",
        prompt_id=first.id,
        repeat=0,
        response_text="Antwort.",
        content_empty=False,
        ttft_s=0.2,
        decode_tps=30.0,
        prefill_tps=90.0,
        e2e_s=1.5,
        prompt_tokens=100,
        completion_tokens=50,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=0.0,
        sys_used_mb=20000.0,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.5,
        reasoning_chars=0,
    )
    (d / "responses.jsonl").write_text(json.dumps(base) + "\n", encoding="utf-8")
    header = [
        "model",
        "variant",
        "metric_type",
        "metric",
        "weight",
        "score",
        "chip",
        "ram_gb",
        "pack",
        "pack_version",
        "quant",
        "ttft_p50",
        "decode_med",
        "peak_ram_gb",
        "model_delta_gb",
        "power",
    ]
    lines = [",".join(header)]
    for dim in pk.dimensions:
        lines.append(
            f"{model},{variant},dimension,{dim.id},{dim.weight},4,"
            f"M5,64,ndassist,1,q4,0.40,18.2,20.0,4.1,ac"
        )
    (d / "scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"{d.name}|{model}|{variant}"


def test_export_meta_csv(tmp_path):
    id1 = _write_bundle(tmp_path / "2026_eval_a", model="a")
    id2 = _write_bundle(tmp_path / "2026_eval_b", model="b")
    r = _client(tmp_path).get(f"/export-meta-csv?rows={id1}&rows={id2}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "meta-leaderboard-2-zellen.csv" in r.headers.get("content-disposition", "")
    body = r.text.splitlines()
    assert body[0].startswith("run_name,model,variant")
    assert len(body) == 3  # header + 2 rows


def test_export_meta_csv_empty_selection_400(tmp_path):
    assert _client(tmp_path).get("/export-meta-csv").status_code == 400
    assert _client(tmp_path).get("/export-meta-csv?rows=nope|x|y").status_code == 400


def test_export_meta_report_md(tmp_path):
    id1 = _write_bundle(tmp_path / "2026_eval_a", model="a")
    id2 = _write_bundle(tmp_path / "2026_eval_b", model="b")
    r = _client(tmp_path).get(f"/export-meta-report?rows={id1}&rows={id2}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "meta-report-2-zellen.md" in r.headers.get("content-disposition", "")
    assert "## Summary" in r.text and "## Detail" in r.text


def test_export_meta_report_blank_suffix_and_no_quality(tmp_path):
    id1 = _write_bundle(tmp_path / "2026_eval_a", model="a")
    r = _client(tmp_path).get(f"/export-meta-report?rows={id1}&judging=0")
    assert r.status_code == 200
    assert "zum-bewerten.md" in r.headers.get("content-disposition", "")
    assert "Quality" not in r.text.split("## Detail")[0]
    assert "## Master-Scorecard" not in r.text


def test_export_meta_report_empty_400(tmp_path):
    assert _client(tmp_path).get("/export-meta-report").status_code == 400


def test_export_meta_report_traversal_rejected(tmp_path):
    # a row id whose run_name escapes runs_dir must not read outside it
    r = _client(tmp_path).get("/export-meta-report?rows=../etc|m|baseline")
    assert r.status_code in (400, 404)  # filtered out (no such pool row) → 400, or guarded → 404
