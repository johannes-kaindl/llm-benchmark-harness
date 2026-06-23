# tests/test_gui_bundle_io.py
import io
import json
import zipfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunRegistry

PACK = "packs/ndassist.yaml"


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


def _resp_dict(model, variant):
    """One EvalResponse round-trip dict for responses.jsonl."""
    from ramcheck.results import EvalResponse

    return EvalResponse(
        pack_id="ndassist",
        pack_version=1,
        machine="t",
        model=model,
        quant="q",
        engine="e",
        engine_version="x",
        variant=variant,
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
    ).as_dict()


def _write_bundle(d, *, groups, scores_by_group, blank_dims=()):
    """Build a self-contained judged bundle under d using the real in-repo pack."""
    from ramcheck.pack import load_pack

    d.mkdir(parents=True, exist_ok=True)
    pk = load_pack(PACK)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "models": [{"id": g[0], "quant": "q"} for g in groups],
                "date": "2026-06-20",
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text(
        "\n".join(json.dumps(_resp_dict(m, v)) for (m, v) in groups) + "\n",
        encoding="utf-8",
    )
    header = ["model", "variant", "metric_type", "metric", "weight", "score"]
    lines = [",".join(header)]
    for m, v in groups:
        scores = scores_by_group[(m, v)]
        for dim in pk.dimensions:
            raw = "" if dim.id in blank_dims else str(scores.get(dim.id, ""))
            lines.append(f"{m},{v},dimension,{dim.id},{dim.weight},{raw}")
    (d / "scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pk


def test_export_bundle_zips_only_ledger_files(tmp_path):
    from ramcheck.pack import load_pack

    d = tmp_path / "2026_eval_nd"
    dims = load_pack(PACK).dimensions
    _write_bundle(
        d,
        groups=[("m", "baseline")],
        scores_by_group={("m", "baseline"): {dim.id: 4 for dim in dims}},
    )
    # transient files that must NOT be exported
    (d / "run.json").write_text("{}", encoding="utf-8")
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")

    r = _client(tmp_path).get(f"/export-bundle/{d.name}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f"{d.name}.zip" in r.headers.get("content-disposition", "")

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = set(z.namelist())
    assert {"bundle.json", "responses.jsonl", "scores.csv"} <= names
    assert "run.json" not in names
    assert "events.jsonl" not in names


def test_export_bundle_missing_bundle_is_404(tmp_path):
    r = _client(tmp_path).get("/export-bundle/does-not-exist")
    assert r.status_code == 404


def test_export_bundle_rejects_path_traversal(tmp_path):
    sensitive = tmp_path.parent / "sensitive"
    sensitive.mkdir(exist_ok=True)
    (sensitive / "bundle.json").write_text("{}", encoding="utf-8")
    r = _client(tmp_path).get("/export-bundle/..%2Fsensitive")
    assert r.status_code in {404, 422}
