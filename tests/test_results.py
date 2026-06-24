def test_evalresponse_reasoning_text_defaults_empty():
    from touchstone.results import EvalResponse

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
    # model-delta fields default safely and serialize
    assert r.sys_used_baseline_mb is None
    assert r.sys_used_delta_mb is None
    d = r.as_dict()
    assert d["sys_used_baseline_mb"] is None
    assert d["sys_used_delta_mb"] is None


def test_evalresponse_reasoning_timing_defaults_safe():
    from touchstone.results import EvalResponse

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
    # new reasoning-timing fields default safely and serialize
    assert r.reasoning_duration_s == 0.0
    assert r.reasoning_tps == 0.0
    assert r.reasoning_completion_tokens == 0
    d = r.as_dict()
    assert d["reasoning_duration_s"] == 0.0
    assert d["reasoning_tps"] == 0.0
    assert d["reasoning_completion_tokens"] == 0


def test_evalresponse_carries_reasoning_timing_when_set():
    from touchstone.results import EvalResponse

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
        response_text="x",
        content_empty=False,
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
        reasoning_duration_s=2.5,
        reasoning_tps=40.0,
        reasoning_completion_tokens=100,
    )
    d = r.as_dict()
    assert d["reasoning_duration_s"] == 2.5
    assert d["reasoning_tps"] == 40.0
    assert d["reasoning_completion_tokens"] == 100


def test_evalresponse_carries_model_delta_when_set():
    from touchstone.results import EvalResponse

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
        response_text="x",
        content_empty=False,
        ttft_s=0.0,
        decode_tps=0.0,
        prefill_tps=0.0,
        e2e_s=0.0,
        prompt_tokens=0,
        completion_tokens=0,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=None,
        sys_used_mb=52000.0,
        mem_pressure_max="",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=0.0,
        sys_used_baseline_mb=40000.0,
        sys_used_delta_mb=12000.0,
    )
    assert r.as_dict()["sys_used_delta_mb"] == 12000.0
    assert r.as_dict()["sys_used_baseline_mb"] == 40000.0
