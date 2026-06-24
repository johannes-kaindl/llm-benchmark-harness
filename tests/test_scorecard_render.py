from touchstone.pack import Pack
from touchstone.results import EvalResponse, ModelReport, Verdict
from touchstone.scorecard import render_scorecard_md, scores_csv_rows


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


def _resp(prompt_id, category, model="m1", sys_used_mb=18000.0, sys_used_delta_mb=None):
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
        sys_used_mb=sys_used_mb,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.0,
        sys_used_delta_mb=sys_used_delta_mb,
    )


def _host():
    return {"chip": "Apple M5 Pro", "ram_gb": "64.0 GB", "macos": "26.5"}


def test_render_unjudged_shows_specs_and_pending_quality():
    pack = _pack()
    responses = [_resp("A1", "A"), _resp("E1", "E")]
    md = render_scorecard_md(pack, responses, [], [], host=_host(), date_str="2026-06-19")
    assert "Demo-Pack" in md
    assert "Apple M5 Pro" in md
    assert "m1" in md  # model in the tech-spec table
    assert "touchstone judge" in md  # quality still pending


def test_render_judged_shows_percent_and_recommendation():
    pack = _pack()
    responses = [_resp("A1", "A"), _resp("E1", "E")]
    verdicts = [
        Verdict("m1", "none", "A1", 0, "A", 5, False, "ok"),
        Verdict("m1", "none", "E1", 0, "E", 4, False, "ok", safety_critical=True),
    ]
    reports = [ModelReport("m1", "none", {"Q1": 5, "Q6": 4})]
    md = render_scorecard_md(
        pack, responses, verdicts, reports, host=_host(), date_str="2026-06-19"
    )
    # weighted: (5*3 + 4*3)=27 of 30 = 90.0%
    assert "90.0" in md
    assert "Ja" in md  # safe + high → recommended


def test_render_judged_knockout_on_low_safety():
    pack = _pack()
    responses = [_resp("E1", "E")]
    verdicts = [Verdict("m1", "none", "E1", 0, "E", 2, True, "schlecht", safety_critical=True)]
    reports = [ModelReport("m1", "none", {"Q1": 5, "Q6": 2})]  # Q6=2 → K.-o.
    md = render_scorecard_md(
        pack, responses, verdicts, reports, host=_host(), date_str="2026-06-19"
    )
    assert "Nein" in md  # knocked out regardless of other scores


def test_scores_csv_rows_are_flat_and_mergeable():
    pack = _pack()
    responses = [_resp("A1", "A")]
    verdicts = [Verdict("m1", "none", "A1", 0, "A", 4, False, "ok")]
    reports = [ModelReport("m1", "none", {"Q1": 4, "Q6": 5})]
    rows = scores_csv_rows(pack, responses, verdicts, reports, host=_host())
    assert rows  # non-empty
    assert all("machine" in r and "model" in r for r in rows)
    # carries hardware + a score column so many machines' CSVs concatenate
    assert any("chip" in r for r in rows)


def test_scores_csv_carries_model_delta_gb():
    pack = _pack()
    # peak 52000 MB, baseline 40000 → delta 12000 MB = 11.72 GB
    responses = [_resp("A1", "A", sys_used_mb=52000.0, sys_used_delta_mb=12000.0)]
    reports = [ModelReport("m1", "none", {"Q1": 4, "Q6": 5})]
    rows = scores_csv_rows(pack, responses, [], reports, host=_host())
    assert all("model_delta_gb" in r for r in rows)
    assert rows[0]["model_delta_gb"] == round(12000.0 / 1024.0, 2)


def test_render_tech_specs_shows_model_delta():
    pack = _pack()
    responses = [_resp("A1", "A", sys_used_mb=52000.0, sys_used_delta_mb=12000.0)]
    md = render_scorecard_md(pack, responses, [], [], host=_host(), date_str="2026-06-23")
    assert "Modell-Delta" in md  # header column
    assert "11.7 GB" in md  # 12000 MB / 1024 ≈ 11.7 GB
