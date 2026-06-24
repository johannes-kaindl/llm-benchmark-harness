# tests/test_gui_app_read.py
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry


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


def _mk_judged(tmp_path):
    d = tmp_path / "2026-06-20_eval_ndassist"
    d.mkdir(parents=True)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "qwen2.5:3b", "quant": "q"}],
                "date": "2026-06-20",
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    return d


def test_overview_lists_bundles(tmp_path):
    _mk_judged(tmp_path)
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "ndassist" in r.text


def test_overview_survives_partial_dim_scores(tmp_path):
    """A judged bundle whose judge omitted a master dimension (score='') must not
    500 the overview: classify succeeds and `/` returns 200 (BLOCKER 1)."""
    from touchstone.gui import bundles
    from touchstone.pack import load_pack

    d = tmp_path / "2026-06-20_eval_ndassist"
    d.mkdir(parents=True)
    pk = load_pack("packs/ndassist.yaml")
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "qwen2.5:3b", "quant": "q"}],
                "date": "2026-06-20",
            }
        ),
        encoding="utf-8",
    )
    from touchstone.results import EvalResponse

    resp = EvalResponse(
        pack_id="ndassist",
        pack_version=1,
        machine="t",
        model="qwen2.5:3b",
        quant="q",
        engine="e",
        engine_version="x",
        variant="baseline",
        category="A",
        prompt_id="A1",
        repeat=0,
        response_text="ok",
        content_empty=False,
        ttft_s=0.1,
        decode_tps=1.0,
        prefill_tps=1.0,
        e2e_s=1.0,
        prompt_tokens=1,
        completion_tokens=1,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=0.0,
        sys_used_mb=0.0,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.0,
        reasoning_chars=0,
    )
    (d / "responses.jsonl").write_text(json.dumps(resp.as_dict()) + "\n", encoding="utf-8")
    # scores.csv: every dimension row present, but one (Q6) has a blank score.
    header = "model,variant,metric_type,metric,weight,score"
    lines = [header]
    for dim in pk.dimensions:
        score = "" if dim.id == "Q6" else "5"
        lines.append(f"qwen2.5:3b,baseline,dimension,{dim.id},{dim.weight},{score}")
    (d / "scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # classify must not raise
    summary = bundles.classify(d)
    assert summary is not None and summary.status == "judged"
    # and the `/` route returns 200 (every bundle visible, none hidden by a 500)
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "ndassist" in r.text


def test_pack_explorer_renders_dimensions(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    # route reads the pack file by path param; ndassist has dimensions Q1..Q7
    assert r.status_code == 200
    assert "Q1" in r.text or "Sicherheit" in r.text


def test_compare_renders_table(tmp_path):
    _mk_judged(tmp_path)
    r = _client(tmp_path).get("/compare")
    assert r.status_code == 200


def test_export_allowed_file(tmp_path):
    d = _mk_judged(tmp_path)
    # scores.csv was created by _mk_judged; verify it is served
    r = _client(tmp_path).get(f"/export/{d.name}/scores.csv")
    assert r.status_code == 200
    assert "metric_type" in r.text


def test_export_disallows_unknown_filename(tmp_path):
    d = _mk_judged(tmp_path)
    r = _client(tmp_path).get(f"/export/{d.name}/secrets.txt")
    assert r.status_code == 404


def test_export_rejects_path_traversal(tmp_path):
    # Plant a file one level above runs_dir to prove it cannot be reached.
    sensitive = tmp_path.parent / "sensitive.csv"
    sensitive.write_text("secret", encoding="utf-8")
    # URL-encoded ".." (%2e%2e) traversal attempt targeting a whitelisted filename.
    r = _client(tmp_path).get("/export/%2e%2e/scores.csv")
    assert r.status_code in {404, 422}  # rejected before or at path resolution
