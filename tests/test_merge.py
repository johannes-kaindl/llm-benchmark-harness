from touchstone.merge import (
    aggregate_window,
    baseline_sys_used_mb,
    load_samples_jsonl,
    merge_run,
    resources_for_window,
)
from touchstone.models import ResourceSample, RunRecord


def _sample(
    ts,
    used=1000.0,
    avail=4000.0,
    swap=0.0,
    rss=2000.0,
    pressure="normal",
    throttled=False,
    baseline=False,
):
    return ResourceSample(
        ts=ts,
        sys_used_mb=used,
        sys_available_mb=avail,
        swap_used_mb=swap,
        server_rss_mb=rss,
        mem_pressure_level=pressure,
        throttled=throttled,
        baseline=baseline,
    )


def _record(t_start=100.0, t_end=102.0):
    return RunRecord(
        run_id="r",
        machine="M",
        model="m",
        quant="q",
        engine="e",
        engine_version="v",
        scenario="rag_synth",
        target_ctx=4096,
        seed=42,
        power_source="ac",
        actual_prompt_tokens=4000,
        completion_tokens=300,
        ttft_s=1.0,
        decode_tps=50.0,
        prefill_tps=4000.0,
        e2e_s=7.0,
        t_start=t_start,
        t_end=t_end,
    )


def test_aggregate_window_peaks_and_swap_delta():
    samples = [
        _sample(100.0, used=1000.0, swap=100.0, rss=2000.0),
        _sample(100.5, used=1500.0, swap=300.0, rss=2500.0, pressure="warn"),
        _sample(101.0, used=1200.0, swap=250.0, rss=2300.0),
    ]
    agg = aggregate_window(samples)
    assert agg.peak_rss_mb == 2500.0
    assert agg.sys_used_mb == 1500.0
    assert agg.swap_delta_mb == 200.0  # 300 - 100
    assert agg.mem_pressure_max == "warn"
    assert agg.n_samples == 3


def test_aggregate_window_empty():
    agg = aggregate_window([])
    assert agg.peak_rss_mb is None
    assert agg.mem_pressure_max == "normal"
    assert agg.throttled is False


def test_merge_run_fills_resource_fields():
    rec = _record(100.0, 102.0)
    samples = [
        _sample(99.0, rss=1000.0),  # just outside but within tolerance
        _sample(100.5, rss=3000.0, throttled=True),
        _sample(101.5, rss=2800.0),
        _sample(200.0, rss=9999.0),  # far outside window — ignored
    ]
    merge_run(rec, samples)
    assert rec.peak_rss_mb == 3000.0
    assert rec.throttled is True
    assert rec.mem_pressure_max in {"normal", "warn", "critical"}


def test_merge_run_falls_back_to_nearest_when_no_window_overlap():
    rec = _record(500.0, 500.1)
    samples = [_sample(100.0, rss=1234.0), _sample(900.0, rss=5678.0)]
    merge_run(rec, samples)
    # nearest to midpoint 500.05 is ts=100 (dist 400) vs ts=900 (dist 400) → first wins on tie
    assert rec.peak_rss_mb in {1234.0, 5678.0}


def test_load_samples_jsonl(tmp_path):
    p = tmp_path / "res.jsonl"
    p.write_text(
        '{"ts": 1.0, "sys_used_mb": 1.0, "sys_available_mb": 2.0, "swap_used_mb": 0.0,'
        ' "server_rss_mb": 5.0, "mem_pressure_level": "normal", "throttled": false}\n',
        encoding="utf-8",
    )
    samples = load_samples_jsonl(p)
    assert len(samples) == 1
    assert samples[0].server_rss_mb == 5.0


def test_load_samples_missing_file_returns_empty(tmp_path):
    assert load_samples_jsonl(tmp_path / "nope.jsonl") == []


def test_baseline_sys_used_prefers_baseline_flagged_sample():
    samples = [
        _sample(99.0, used=40000.0, baseline=True),
        _sample(100.0, used=52000.0),
        _sample(101.0, used=48000.0),
    ]
    assert baseline_sys_used_mb(samples) == 40000.0


def test_baseline_sys_used_falls_back_to_min_when_unflagged():
    samples = [
        _sample(99.0, used=41000.0),
        _sample(100.0, used=52000.0),
        _sample(101.0, used=39000.0),
    ]
    # no baseline marker → min of all samples is the pre-run floor
    assert baseline_sys_used_mb(samples) == 39000.0


def test_baseline_sys_used_none_when_no_samples():
    assert baseline_sys_used_mb([]) is None


def test_aggregate_window_computes_model_delta_against_baseline():
    # baseline tick 40000 MB; peak inside the window 52000 MB → delta 12000
    window = [
        _sample(100.0, used=48000.0),
        _sample(100.5, used=52000.0),
        _sample(101.0, used=50000.0),
    ]
    agg = aggregate_window(window, baseline_mb=40000.0)
    assert agg.sys_used_mb == 52000.0
    assert agg.sys_used_baseline_mb == 40000.0
    assert agg.sys_used_delta_mb == 12000.0


def test_resources_for_window_threads_baseline_from_full_sample_list():
    # The baseline tick lives OUTSIDE the run window (captured pre-first-request);
    # resources_for_window must still subtract it from the in-window peak.
    samples = [
        _sample(50.0, used=40000.0, baseline=True),  # pre-run, outside [100,102]
        _sample(100.5, used=52000.0),
        _sample(101.5, used=50000.0),
    ]
    agg = resources_for_window(samples, 100.0, 102.0)
    assert agg.sys_used_mb == 52000.0
    assert agg.sys_used_delta_mb == 12000.0


def test_merge_run_fills_model_delta():
    rec = _record(100.0, 102.0)
    samples = [
        _sample(50.0, used=40000.0, baseline=True),
        _sample(100.5, used=52000.0, rss=3000.0),
        _sample(101.5, used=50000.0, rss=2800.0),
    ]
    merge_run(rec, samples)
    assert rec.sys_used_mb == 52000.0
    assert rec.sys_used_delta_mb == 12000.0
