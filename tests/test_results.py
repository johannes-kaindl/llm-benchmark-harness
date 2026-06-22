def test_evalresponse_reasoning_text_defaults_empty():
    from ramcheck.results import EvalResponse

    r = EvalResponse(
        pack_id="p",
        pack_version=1,
        machine="M",
        model="m",
        quant="",
        engine="e",
        engine_version="0",
        variant="none",
        category="A",
        prompt_id="A1",
        repeat=0,
        response_text="",
        content_empty=True,
        ttft_s=0.0,
        decode_tps=0.0,
        prefill_tps=0.0,
        e2e_s=0.0,
        prompt_tokens=0,
        completion_tokens=0,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=None,
        sys_used_mb=None,
        mem_pressure_max="",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=0.0,
    )
    assert r.reasoning_text == ""
    assert r.as_dict()["reasoning_text"] == ""
