# tests/test_gui_bundle_io.py
import io
import json
import zipfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui import bundles
from touchstone.gui.control import RunRegistry

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
    from touchstone.results import EvalResponse

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
    from touchstone.pack import load_pack

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
    from touchstone.pack import load_pack

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


def test_config_page_renders_import_card(tmp_path):
    r = _client(tmp_path).get("/config")
    assert r.status_code == 200
    assert 'action="/import-bundle"' in r.text
    assert 'enctype="multipart/form-data"' in r.text


def _zip_bundle(d, *, files=("bundle.json", "responses.jsonl", "scores.csv")):
    """Zip the named ledger files of a built bundle dir into in-memory bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            p = d / f
            if p.exists():
                z.write(p, arcname=f)
    return buf.getvalue()


def _judged_zip(tmp_path, name="2026_eval_nd"):
    """Build a judged bundle in a scratch dir (outside runs_dir) and return its zip bytes."""
    from touchstone.pack import load_pack

    src = tmp_path / "_scratch" / name
    dims = load_pack(PACK).dimensions
    _write_bundle(
        src,
        groups=[("m", "baseline")],
        scores_by_group={("m", "baseline"): {dim.id: 4 for dim in dims}},
    )
    return _zip_bundle(src), src.name


def test_import_bundle_lands_judged_dir(tmp_path):
    payload, name = _judged_zip(tmp_path)
    r = _client(tmp_path).post(
        "/import-bundle", files={"file": (f"{name}.zip", payload, "application/zip")}
    )
    assert r.status_code == 200
    new_name = r.json()["run_dir"]
    new_dir = tmp_path / new_name
    assert new_dir.is_dir()
    summary = bundles.classify(new_dir)
    assert summary is not None and summary.status == "judged"


def test_import_bundle_missing_bundle_json_is_400(tmp_path):
    payload, _name = _judged_zip(tmp_path)
    # strip bundle.json out of the zip → invalid bundle
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        keep = [n for n in z.namelist() if n != "bundle.json"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
            for n in keep:
                out.writestr(n, z.read(n))
    r = _client(tmp_path).post(
        "/import-bundle", files={"file": ("broken.zip", buf.getvalue(), "application/zip")}
    )
    assert r.status_code == 400


def test_import_bundle_name_collision_is_suffixed(tmp_path):
    payload, name = _judged_zip(tmp_path)
    client = _client(tmp_path)
    first = client.post(
        "/import-bundle", files={"file": (f"{name}.zip", payload, "application/zip")}
    )
    second = client.post(
        "/import-bundle", files={"file": (f"{name}.zip", payload, "application/zip")}
    )
    assert first.status_code == 200 and second.status_code == 200
    n1, n2 = first.json()["run_dir"], second.json()["run_dir"]
    assert n1 != n2
    assert (tmp_path / n1).is_dir() and (tmp_path / n2).is_dir()


def _zip_from_members(members):
    """Build an in-memory zip from {arcname: bytes|str}. Allows unsafe arcnames for slip tests."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in members.items():
            z.writestr(arc, data)
    return buf.getvalue()


def test_import_bundle_rejects_zip_slip(tmp_path):
    # A member that escapes the extraction root must be rejected before any extraction.
    escape_target = tmp_path.parent / "escape.txt"
    if escape_target.exists():
        escape_target.unlink()
    payload = _zip_from_members({"bundle.json": "{}", "../escape.txt": "pwned"})
    r = _client(tmp_path).post(
        "/import-bundle", files={"file": ("evil.zip", payload, "application/zip")}
    )
    assert r.status_code == 400
    assert "unsafe zip entry" in r.text
    assert not escape_target.exists()  # nothing written outside runs_dir


def test_import_bundle_not_a_zip_is_400(tmp_path):
    r = _client(tmp_path).post(
        "/import-bundle", files={"file": ("x.zip", b"this is not a zip", "application/zip")}
    )
    assert r.status_code == 400
    assert "not a zip" in r.text


def test_import_bundle_bad_responses_line_is_400(tmp_path):
    payload = _zip_from_members({"bundle.json": "{}", "responses.jsonl": "{}\nnot json at all\n"})
    r = _client(tmp_path).post(
        "/import-bundle", files={"file": ("b.zip", payload, "application/zip")}
    )
    assert r.status_code == 400
    assert "invalid responses.jsonl" in r.text


def test_import_bundle_rejects_too_many_members(tmp_path):
    # zip-bomb guard: a member count over the cap is rejected before extractall.
    members = {f"f{i}.txt": "" for i in range(10_001)}
    members["bundle.json"] = "{}"
    payload = _zip_from_members(members)
    r = _client(tmp_path).post(
        "/import-bundle", files={"file": ("bomb.zip", payload, "application/zip")}
    )
    assert r.status_code == 400
    assert "too many zip entries" in r.text
