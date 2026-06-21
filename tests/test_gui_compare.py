from __future__ import annotations

from ramcheck.gui import compare
from ramcheck.models import ResourceSample
from ramcheck.results import EvalResponse


def _resp(model: str, variant: str, **over) -> EvalResponse:
    base = dict(
        pack_id="ndassist", pack_version=1, machine="t", model=model, quant="q",
        engine="e", engine_version="x", variant=variant, category="A", prompt_id="A1",
        repeat=0, response_text="ok", content_empty=False, ttft_s=0.1, decode_tps=10.0,
        prefill_tps=1.0, e2e_s=1.0, prompt_tokens=1, completion_tokens=1,
        is_cold_start=False, power_source="ac", peak_rss_mb=0.0, sys_used_mb=8000.0,
        mem_pressure_max="normal", throttled=False, ok=True, error="", seed=42,
        t_start=0.0, t_end=1.0, reasoning_chars=0,
    )
    base.update(over)
    return EvalResponse(**base)


def _sample(ts: float, cpu: float | None) -> ResourceSample:
    return ResourceSample(
        ts=ts, sys_used_mb=1.0, sys_available_mb=1.0, swap_used_mb=0.0,
        server_rss_mb=None, mem_pressure_level="normal", throttled=False, cpu_pct=cpu,
    )


def test_axis_options_multi_variant_single_model():
    responses = [_resp("m", "baseline"), _resp("m", "none")]
    opts = compare.axis_options(responses)
    assert opts.models == ["m"]
    assert opts.variants == ["baseline", "none"]
    assert opts.default_axis == "variant"
    assert opts.default_label == "Variante"
    assert opts.comparable is True


def test_axis_options_multi_model_defaults_to_model_axis():
    responses = [_resp("a", "baseline"), _resp("b", "baseline")]
    opts = compare.axis_options(responses)
    assert opts.default_axis == "model"
    assert opts.default_label == "Modell"
    assert opts.comparable is True


def test_axis_options_single_everything_not_comparable():
    opts = compare.axis_options([_resp("m", "baseline")])
    assert opts.comparable is False


def test_cpu_for_window_none_ticks_yield_na():
    # the real state today: ticks exist but carry no cpu_pct -> "n. v." (None, None)
    responses = [_resp("m", "baseline", t_start=0.0, t_end=2.0)]
    samples = [_sample(0.5, None), _sample(1.5, None)]
    assert compare._cpu_for_window(samples, responses) == (None, None)


def test_cpu_for_window_max_and_avg():
    responses = [_resp("m", "baseline", t_start=0.0, t_end=2.0)]
    samples = [_sample(0.5, 40.0), _sample(1.5, 60.0), _sample(9.9, 100.0)]  # last outside window
    cpu_max, cpu_avg = compare._cpu_for_window(samples, responses)
    assert cpu_max == 60.0
    assert cpu_avg == 50.0


def test_cpu_for_window_empty_samples():
    assert compare._cpu_for_window([], [_resp("m", "baseline")]) == (None, None)
