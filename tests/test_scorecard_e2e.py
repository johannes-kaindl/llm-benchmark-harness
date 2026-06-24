import math

from touchstone.results import EvalResponse
from touchstone.scorecard import _perf_summary


def _resp(*, e2e_s, ok=True, is_cold_start=False):
    return EvalResponse(
        pack_id="demo",
        pack_version=1,
        machine="M",
        model="m1",
        quant="n/a",
        engine="ollama",
        engine_version="0",
        variant="none",
        category="A",
        prompt_id="A1",
        repeat=0,
        response_text="x",
        content_empty=False,
        ttft_s=0.2,
        decode_tps=12.0,
        prefill_tps=100.0,
        e2e_s=e2e_s,
        prompt_tokens=10,
        completion_tokens=20,
        is_cold_start=is_cold_start,
        power_source="ac",
        peak_rss_mb=None,
        sys_used_mb=18000.0,
        mem_pressure_max="normal",
        throttled=False,
        ok=ok,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.0,
    )


def test_perf_summary_includes_e2e_med():
    resps = [_resp(e2e_s=1.0, ok=True), _resp(e2e_s=3.0, ok=True)]
    p = _perf_summary(resps)
    assert p["e2e_med"] == 2.0


def test_perf_summary_e2e_med_ignores_nan_e2e():
    resps = [_resp(e2e_s=2.0, ok=True), _resp(e2e_s=math.nan, ok=True)]
    p = _perf_summary(resps)
    assert p["e2e_med"] == 2.0
