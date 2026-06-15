from ramcheck import report
from ramcheck.models import RunRecord


def _rec(**kw):
    base = dict(
        run_id="r",
        machine="M1",
        model="qwen3-8b",
        quant="Q5",
        engine="lm-studio",
        engine_version="0.3",
        scenario="bodydouble",
        target_ctx=0,
        seed=42,
        power_source="ac",
        actual_prompt_tokens=80,
        completion_tokens=120,
        ttft_s=0.3,
        decode_tps=40.0,
        prefill_tps=260.0,
        e2e_s=3.0,
        t_start=0.0,
        t_end=3.0,
    )
    base.update(kw)
    return RunRecord(**base)


def test_aggregate_excludes_warmup_throttled_battery_cold():
    records = [
        _rec(warmup=True, ttft_s=9.9),  # warmup → excluded
        _rec(is_cold_start=True, ttft_s=8.8),  # cold → excluded (own section)
        _rec(throttled=True, ttft_s=7.7),  # throttled → excluded
        _rec(power_source="battery", ttft_s=6.6),  # battery → excluded
        _rec(ttft_s=0.30),
        _rec(ttft_s=0.40),
        _rec(ttft_s=0.50),
    ]
    cells = report.aggregate_cells(records)
    assert len(cells) == 1
    cell = cells[0]
    assert cell.n_valid == 3
    assert cell.n_excluded_throttled == 1
    assert cell.n_excluded_battery == 1
    # P50 of [0.3,0.4,0.5] = 0.4, untouched by the excluded outliers
    assert abs(cell.ttft_p50 - 0.40) < 1e-9


def test_peak_ram_uses_system_memory_with_rss_hint():
    # System memory is the headline; server RSS shown as a parenthetical hint.
    records = [
        _rec(sys_used_mb=20480.0, peak_rss_mb=300.0),
        _rec(sys_used_mb=21504.0, peak_rss_mb=320.0),
    ]
    cells = report.aggregate_cells(records)
    assert cells[0].peak_sys_used_mb == 21504.0  # peak of the two
    assert cells[0].peak_rss_mb == 320.0
    md = report.render_report_md(records, date_str="2026-06-15")
    assert "21.0 GB sys" in md
    assert "RSS 0.3 GB" in md


def test_low_n_cell_flagged_and_marked():
    # Only 3 valid runs → below the MIN_VALID_RUNS floor of 7.
    records = [_rec(ttft_s=0.3), _rec(ttft_s=0.4), _rec(ttft_s=0.5)]
    cells = report.aggregate_cells(records)
    assert cells[0].low_n is True
    md = report.render_report_md(records, date_str="2026-06-15")
    assert "⚠️ n=3" in md


def test_full_n_cell_not_flagged():
    records = [_rec(ttft_s=0.3) for _ in range(7)]
    cells = report.aggregate_cells(records)
    assert cells[0].low_n is False


def test_render_report_has_ssot_columns_and_metadata():
    records = [_rec(), _rec(ttft_s=0.4), _rec(is_cold_start=True, ttft_s=5.0)]
    md = report.render_report_md(
        records,
        date_str="2026-06-15",
        host={"chip": "Apple M1", "macos": "15.5", "ram_gb": "16 GB"},
    )
    assert "TTFT P50/P95 (s)" in md
    assert "Konsist. (CV%)" in md
    assert "Throttle/Akku" in md
    assert "Cold-Start" in md  # cold-start section rendered
    assert "Apple M1" in md  # header metadata
    assert "lm-studio 0.3" in md  # engine line


def test_raw_csv_roundtrip(tmp_path):
    records = [
        _rec(),
        _rec(scenario="rag_synth", target_ctx=4096, peak_rss_mb=5000.0, mem_pressure_max="warn"),
    ]
    path = tmp_path / "raw.csv"
    report.write_raw_csv(records, path)
    loaded = report.load_raw_csv(path)
    assert len(loaded) == 2
    assert loaded[1].scenario == "rag_synth"
    assert loaded[1].target_ctx == 4096
    assert loaded[1].peak_rss_mb == 5000.0
    assert loaded[1].mem_pressure_max == "warn"


def test_write_report_creates_both_files(tmp_path):
    md_path, raw_path = report.write_report([_rec()], tmp_path, date_str="2026-06-15")
    assert md_path.exists() and raw_path.exists()
    assert "ramcheck" in md_path.read_text(encoding="utf-8")
