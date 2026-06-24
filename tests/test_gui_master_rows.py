from touchstone import scorecard
from touchstone.pack import load_pack
from touchstone.results import EvalResponse, ModelReport, Verdict


def _resp(model="m", variant="baseline"):
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
    )


def test_master_rows_public_matches_rubric_level():
    pk = load_pack("packs/ndassist.yaml")
    responses = [_resp()]
    verdicts = [Verdict("m", "baseline", "A1", 0, "A", 5, False, "good", False, False)]
    # all master dims = 5 → 100% → rubric_level 'hoch', safety passes
    reports = [ModelReport("m", "baseline", {d.id: 5 for d in pk.dimensions}, {})]
    rows = scorecard.master_rows(pk, responses, verdicts, reports)
    assert len(rows) == 1
    assert rows[0]["model"] == "m" and rows[0]["variant"] == "baseline"
    row = rows[0]
    assert row["rubric_level"] in {"hoch", "solide", "teilweise", "ungenügend"}
    assert row["safety_passed"] is True
    assert "recommendation" not in row
