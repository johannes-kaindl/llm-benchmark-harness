"""Task 5 — machine label removed from quality/result path.

Verifies that:
1. scores_csv_rows no longer emits a 'machine' key.
2. aggregate.AggRow has no 'machine' attribute.
3. render_aggregate_md produces no 'Maschine' column.
4. The /compare route renders without 'Maschine' in the table header.

Helpers are copied from tests/test_scorecard_render.py so this file is
self-contained and does not depend on the state of that module.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from touchstone import aggregate as agg_mod
from touchstone.pack import Pack
from touchstone.results import EvalResponse, ModelReport, Verdict
from touchstone.scorecard import scores_csv_rows

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_scorecard_render.py)
# ---------------------------------------------------------------------------


def _pack():
    return Pack.model_validate(
        {
            "id": "demo",
            "title": "Demo-Pack",
            "scale": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"},
            "dimensions": [
                {"id": "Q1", "name": "Korrektheit", "weight": 3},
                {"id": "Q6", "name": "Sicherheit", "weight": 3},
            ],
            "ko_rule": {"dimension": "Q6", "threshold": 2, "red_flag_prompts": ["E1"]},
            "prompt_variants": [{"id": "none", "system_prompt": None}],
            "categories": [
                {"id": "A", "name": "ADHS", "prompts": [{"id": "A1", "title": "t", "prompt": "p"}]},
                {
                    "id": "E",
                    "name": "Safety",
                    "prompts": [{"id": "E1", "title": "t", "prompt": "p"}],
                },
            ],
        }
    )


def _resp(prompt_id, category, model="m1"):
    return EvalResponse(
        pack_id="demo",
        pack_version=1,
        machine="M",
        model=model,
        quant="n/a",
        engine="ollama",
        engine_version="0",
        variant="none",
        category=category,
        prompt_id=prompt_id,
        repeat=0,
        response_text="x",
        content_empty=False,
        ttft_s=0.2,
        decode_tps=12.0,
        prefill_tps=100.0,
        e2e_s=1.0,
        prompt_tokens=10,
        completion_tokens=20,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=None,
        sys_used_mb=18000.0,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.0,
        sys_used_delta_mb=None,
    )


def _host():
    return {"chip": "Apple M5 Pro", "ram_gb": "64.0 GB", "macos": "26.5"}


# ---------------------------------------------------------------------------
# GUI helper
# ---------------------------------------------------------------------------


class _FakeLauncher:
    def spawn(self, argv):
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def _client(tmp_path):
    from touchstone.gui import app as gui_app
    from touchstone.gui.control import RunRegistry

    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _write_scores_bundle(d, *, chip, ram_gb):
    """Minimal judged bundle — no machine column — so /compare aggregate works."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-24",
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    header = [
        "chip",
        "ram_gb",
        "pack",
        "pack_version",
        "model",
        "quant",
        "variant",
        "metric_type",
        "metric",
        "weight",
        "score",
    ]
    row = [chip, ram_gb, "ndassist", "1", "m", "q", "baseline", "dimension", "Q1", "2", "4"]
    (d / "scores.csv").write_text(",".join(header) + "\n" + ",".join(row) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_scores_csv_rows_has_no_machine_key():
    """scores_csv_rows must not emit 'machine' — auto-detected chip/ram are canonical."""
    pack = _pack()
    responses = [_resp("A1", "A")]
    verdicts = [Verdict("m1", "none", "A1", 0, "A", 4, False, "ok")]
    reports = [ModelReport("m1", "none", {"Q1": 4, "Q6": 5})]
    rows = scores_csv_rows(pack, responses, verdicts, reports, host=_host())
    assert rows, "scores_csv_rows must return at least one row"
    assert "chip" in rows[0], "chip column must be present"
    assert "ram_gb" in rows[0], "ram_gb column must be present"
    assert "machine" not in rows[0], "'machine' must be removed from scores.csv output"


def test_agg_row_has_no_machine_attribute():
    """AggRow must not carry a 'machine' field after Task 5."""
    rows = [
        {
            "chip": "M5",
            "ram_gb": "64",
            "pack": "nd",
            "pack_version": "1",
            "model": "m",
            "quant": "q",
            "variant": "baseline",
            "ttft_p50": "0.3",
            "decode_med": "60",
            "e2e_med": "1.0",
            "peak_ram_gb": "40",
            "model_delta_gb": "",
            "power": "ac",
            "metric_type": "dimension",
            "metric": "Q1",
            "weight": "3",
            "score": "4",
        }
    ]
    out = agg_mod.aggregate(rows)
    assert len(out) == 1
    assert not hasattr(out[0], "machine"), "AggRow must not have a 'machine' attribute"


def test_render_aggregate_md_has_no_maschine_column():
    """render_aggregate_md must not produce a 'Maschine' column."""
    rows = [
        {
            "chip": "M5",
            "ram_gb": "64",
            "pack": "nd",
            "pack_version": "1",
            "model": "m",
            "quant": "q",
            "variant": "baseline",
            "metric_type": "dimension",
            "metric": "Q1",
            "weight": "3",
            "score": "4",
        }
    ]
    md = agg_mod.render_aggregate_md(agg_mod.aggregate(rows))
    assert "Maschine" not in md, "Maschine column must be removed from aggregate markdown"
    assert "Chip" in md, "Chip column must remain"
    assert "Qualität" in md, "Qualität column must remain"


def test_compare_route_has_no_maschine_column(tmp_path):
    """/compare must render without a 'Maschine' table header."""
    _write_scores_bundle(tmp_path / "2026_eval_nd", chip="Apple M5 Pro", ram_gb="64.0")
    r = _client(tmp_path).get("/compare")
    assert r.status_code == 200, f"/compare returned {r.status_code}"
    assert "Maschine" not in r.text, "'Maschine' column must not appear in /compare response"
