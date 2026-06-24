# tests/test_gui_report_md.py
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunRegistry
from ramcheck.gui.glossary import GLOSSARY
from ramcheck.gui.report_md import render_report_md
from ramcheck.pack import load_pack

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


def _resp_dict(model, variant, prompt_id, **over):
    from ramcheck.results import EvalResponse

    base = dict(
        pack_id="ndassist",
        pack_version=1,
        machine="t",
        model=model,
        quant="q",
        engine="e",
        engine_version="x",
        variant=variant,
        category="A",
        prompt_id=prompt_id,
        repeat=0,
        response_text="Eine vollständige, hilfreiche Antwort.",
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
        sys_used_mb=8000.0,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.5,
        reasoning_chars=0,
    )
    base.update(over)
    return EvalResponse(**base).as_dict()


def _write_bundle(d, *, model="m", variant="baseline"):
    """A self-contained judged bundle over the real in-repo pack (one answer per prompt)."""
    pk = load_pack(PACK)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "models": [{"id": model, "quant": "q"}],
                "date": "2026-06-24",
                "seed": 42,
            }
        ),
        encoding="utf-8",
    )
    first = next(p for _, p in pk.all_prompts())
    (d / "responses.jsonl").write_text(
        json.dumps(_resp_dict(model, variant, first.id)) + "\n", encoding="utf-8"
    )
    header = ["model", "variant", "metric_type", "metric", "weight", "score"]
    lines = [",".join(header)]
    for dim in pk.dimensions:
        lines.append(f"{model},{variant},dimension,{dim.id},{dim.weight},4")
    (d / "scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pk, first


def test_export_report_route_serves_full_markdown(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _pk, first = _write_bundle(d)
    r = _client(tmp_path).get(f"/export-report/{d.name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert f"{d.name}.md" in r.headers.get("content-disposition", "")
    body = r.text
    # title + all the major sections
    assert f"# Ergebnis-Report — {d.name}" in body
    for section in ("Bewertungs-Methode", "Prompts & Antworten", "Metrik-Glossar", "Dimensionen"):
        assert f"## {section}" in body or section in body
    # full prompt text is present and NOT truncated (the real prompt is > 80 chars)
    assert len(first.prompt) > 40
    assert first.prompt[:60] in body
    # per-answer measurements + a glossary link + internal anchors
    assert "tok/s" in body
    assert "(#glossar-" in body  # a metric links into the glossary
    assert f'<a id="prompt-{first.id}"></a>' in body
    # the glossary section actually defines a term
    assert GLOSSARY["ttft_p50"].short in body


def test_export_report_missing_bundle_is_404(tmp_path):
    r = _client(tmp_path).get("/export-report/does-not-exist")
    assert r.status_code == 404


def test_render_report_md_fences_arbitrary_backticks():
    # A model answer that itself contains a ``` code fence must not break out of its block.
    from ramcheck.results import EvalResponse

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    payload = _resp_payload(first.id)
    payload["response_text"] = "Hier Code:\n```python\nprint('x')\n```\nfertig."
    payload["reasoning_text"] = ""
    detail = {
        "run_dir": Path("runs/x"),
        "manifest": {},
        "pack": pk,
        "responses": [EvalResponse(**payload)],
        "verdicts": [],
        "reports": [],
        "master_rows": [],
        "cited_ids": {},
    }
    md = render_report_md(detail, GLOSSARY)
    assert "````" in md  # outer fence widened to >3 backticks
    assert "print('x')" in md  # the inner content is preserved verbatim


def test_render_report_md_surfaces_reasoning_and_ram(tmp_path):
    # Pure render: a reasoning answer with baseline-delta RAM → both surface with units.
    from ramcheck.results import EvalResponse

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp = EvalResponse(**_resp_payload(first.id))
    detail = {
        "run_dir": Path("runs/x"),
        "manifest": {"host": {"chip": "Apple M5 Pro", "ram_gb": "64.0"}, "date": "2026-06-24"},
        "pack": pk,
        "responses": [resp],
        "verdicts": [],
        "reports": [],
        "master_rows": [],
        "cited_ids": {},
    }
    md = render_report_md(detail, GLOSSARY)
    assert "Thinking-Dauer" in md
    assert "2.50 s" in md  # reasoning_duration_s
    assert "11.7 GB" in md  # sys_used_delta_mb 12000 / 1024
    assert first.prompt[:60] in md
    assert "## Metrik-Glossar" in md


def _resp_payload(prompt_id):
    from ramcheck.results import EvalResponse  # noqa: F401

    return dict(
        pack_id="ndassist",
        pack_version=1,
        machine="t",
        model="m",
        quant="q",
        engine="e",
        engine_version="x",
        variant="baseline",
        category="A",
        prompt_id=prompt_id,
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
        reasoning_chars=120,
        reasoning_text="kurzes Nachdenken",
        reasoning_duration_s=2.5,
        reasoning_tps=40.0,
        reasoning_completion_tokens=80,
        sys_used_baseline_mb=8000.0,
        sys_used_delta_mb=12000.0,
    )
