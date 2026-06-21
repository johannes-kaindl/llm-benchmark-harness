# tests/test_gui_app_detail.py
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunRegistry
from ramcheck.judge import write_reports_jsonl
from ramcheck.results import ModelReport, Verdict


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


def _mk(tmp_path):
    """Bundle with a real EvalResponse so master_rows() produces a non-empty result."""
    d = tmp_path / "2026_eval_nd"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-21",
                "host": {},
            }
        ),
        encoding="utf-8",
    )
    # A minimal EvalResponse so model_variant_groups() sees ("m", "none")
    resp = {
        "pack_id": "ndassist",
        "pack_version": 1,
        "machine": "t",
        "model": "m",
        "quant": "q",
        "engine": "llama.cpp",
        "engine_version": "0",
        "variant": "none",
        "category": "Sicherheit",
        "prompt_id": "E1",
        "repeat": 0,
        "response_text": "ok",
        "content_empty": False,
        "ttft_s": 0.1,
        "decode_tps": 10.0,
        "prefill_tps": 10.0,
        "e2e_s": 0.2,
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "is_cold_start": False,
        "power_source": "ac",
        "peak_rss_mb": None,
        "sys_used_mb": None,
        "mem_pressure_max": "",
        "throttled": False,
        "ok": True,
        "error": "",
        "seed": 42,
        "t_start": 0.0,
        "t_end": 0.2,
    }
    (d / "responses.jsonl").write_text(json.dumps(resp) + "\n", encoding="utf-8")
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    write_reports_jsonl(
        d / "reports.jsonl",
        [ModelReport("m", "none", {"Q6": 2}, {"Q6": "schwach bei E1"})],
    )
    v = Verdict(
        model="m",
        variant="none",
        prompt_id="E1",
        repeat=0,
        category="Sicherheit",
        score=2,
        red_flag=True,
        rationale="Krise nicht erkannt",
    )
    (d / "judgements.jsonl").write_text(json.dumps(v.as_dict()) + "\n", encoding="utf-8")
    return d


def test_result_renders_scorecard_and_rationale(tmp_path):
    """Scorecard block and dim-rationale text appear in the rendered result HTML.

    The KO-branch data layer is covered by test_gui_bundle_detail.py::test_bundle_detail_ko_branches_and_cited_ids.
    """
    _mk(tmp_path)
    r = _client(tmp_path).get("/result/2026_eval_nd")
    assert r.status_code == 200
    # The Scorecard block and the Q6 rationale must be present in the rendered HTML
    assert "Scorecard" in r.text
    assert "schwach bei E1" in r.text


def test_pack_explainer_present(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    assert r.status_code == 200
    assert "holistisch" in r.text.lower()  # the method explainer (L9)
