"""TTFT aggregates must not silently measure the prefix cache instead of the latency.

The latency runner sends the SAME prompt `runs_per_cell` times per cell. On an engine with a
prefix cache (LM Studio, Splash, vLLM) every run after the first skips most of the prefill, so the
warm aggregates measure the cache: against Splash the TTFT-P50 read 0.21 s while the cold first run
took 7.3 s. The honest latency number is the FIRST request of each cell — which the runner already
makes (cold-start for cell 0, the discarded warmup for every other cell) and then threw away.

(a) report that first request per cell as "kalt" next to the warm aggregates;
(c) record `usage.prompt_tokens_details.cached_tokens` where the engine sends it, so a bundle
    PROVES which row was a cache hit instead of leaving it to inference.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from touchstone import report
from touchstone.client import OpenAIStreamClient
from touchstone.config import Config
from touchstone.models import RunRecord
from touchstone.runner import StreamEvent, run_benchmark


def _rec(**kw: object) -> RunRecord:
    base: dict[str, object] = dict(
        run_id="r", machine="M5", model="qwen", quant="4bit", engine="lm-studio",
        engine_version="0.4", scenario="rag_synth", target_ctx=4096, seed=42, power_source="ac",
        actual_prompt_tokens=4000, completion_tokens=100, ttft_s=0.2, decode_tps=40.0,
        prefill_tps=20000.0, e2e_s=3.0, t_start=0.0, t_end=3.0,
    )  # fmt: skip
    base.update(kw)
    return RunRecord(**base)  # type: ignore[arg-type]


# --- (c) cached_tokens: engine → StreamEvent → RunRecord → raw.csv --------------------------


def _usage_chunk(**usage: object) -> SimpleNamespace:
    return SimpleNamespace(choices=[], usage=SimpleNamespace(**usage))


def test_client_reads_cached_tokens_from_prompt_tokens_details(monkeypatch):  # type: ignore[no-untyped-def]
    client = OpenAIStreamClient("http://localhost:9/v1")
    chunk = _usage_chunk(
        prompt_tokens=4000,
        completion_tokens=10,
        prompt_tokens_details=SimpleNamespace(cached_tokens=3968),
    )
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kw: [chunk])
    evs = list(client.stream(messages=[], model="m", max_tokens=1, temperature=0.0, seed=0))
    assert [e.cached_tokens for e in evs if e.prompt_tokens is not None] == [3968]


def test_client_leaves_cached_tokens_none_when_engine_does_not_report_it(monkeypatch):  # type: ignore[no-untyped-def]
    """Absent is not zero: an engine without the field must not read as 'nothing was cached'."""
    client = OpenAIStreamClient("http://localhost:9/v1")
    for chunk in (
        _usage_chunk(prompt_tokens=10, completion_tokens=1),  # no details at all
        _usage_chunk(prompt_tokens=10, completion_tokens=1, prompt_tokens_details=None),
    ):
        monkeypatch.setattr(client._client.chat.completions, "create", lambda c=chunk, **kw: [c])
        evs = list(client.stream(messages=[], model="m", max_tokens=1, temperature=0.0, seed=0))
        assert [e.cached_tokens for e in evs if e.prompt_tokens is not None] == [None]


class _CachingClient:
    """Stand-in for a prefix-caching engine: the first request of a prompt is a miss, every
    repeat of the same prompt reports (almost) the whole prompt as cached."""

    engine = "fake"
    engine_version = "1"

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def stream(self, *, messages, model, max_tokens, temperature, seed, extra_body=None):  # type: ignore[no-untyped-def]
        key = repr(messages)
        cached = 96 if key in self.seen else 0
        self.seen.add(key)
        yield StreamEvent(delta_text="ok")
        yield StreamEvent(prompt_tokens=100, completion_tokens=1, cached_tokens=cached)


class _NoopSampler:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_run_benchmark_records_cached_tokens_per_request(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr("touchstone.sampler.read_power_source", lambda: "ac")
    cfg = Config.model_validate(
        {
            "endpoint": {"base_url": "http://localhost:1234/v1"},
            "machine": "M5-test",
            "runs_per_cell": 3,
            "context_buckets": [4096],
            "scenarios": ["bodydouble", "rag_synth"],
            "models": [{"id": "m", "quant": "q"}],
            "power_check": False,
            "output_dir": str(tmp_path),
        }
    )
    records = run_benchmark(cfg, _CachingClient(), run_dir=tmp_path / "run", sampler=_NoopSampler())
    # cell 0: cold-start is the miss, its warmup and measured runs are hits
    # cell 1: the warmup is the miss, the measured runs are hits
    by_id = {r.run_id: r.cached_tokens for r in records}
    assert by_id["cold-0"] == 0 and by_id["0-0"] == 96 and by_id["0-1"] == 96
    assert by_id["1-0"] == 0 and by_id["1-1"] == 96 and by_id["1-2"] == 96


def test_raw_csv_roundtrips_cached_tokens_and_optional_numbers(tmp_path):  # type: ignore[no-untyped-def]
    """Optional numeric fields must come back as numbers. `sys_used_delta_mb` used to come back as
    the STRING '5000.0' — the loader recognised optional floats by a hard-coded name list that
    missed it, and a new optional field would have fallen into the same hole."""
    path = tmp_path / "raw.csv"
    report.write_raw_csv(
        [_rec(cached_tokens=3968, sys_used_delta_mb=5000.0), _rec(cached_tokens=None)], path
    )
    hit, unknown = report.load_raw_csv(path)
    assert hit.cached_tokens == 3968 and isinstance(hit.cached_tokens, int)
    assert hit.sys_used_delta_mb == 5000.0 and isinstance(hit.sys_used_delta_mb, float)
    assert unknown.cached_tokens is None  # absent stays absent, never 0
    assert unknown.sys_used_delta_mb is None


def test_old_raw_csv_without_cached_tokens_column_still_loads(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "raw.csv"
    report.write_raw_csv([_rec()], path)
    lines = path.read_text(encoding="utf-8").splitlines()
    cols = lines[0].split(",")
    idx = cols.index("cached_tokens")
    stripped = [",".join(v for i, v in enumerate(ln.split(",")) if i != idx) for ln in lines]
    path.write_text("\n".join(stripped) + "\n", encoding="utf-8")
    (r,) = report.load_raw_csv(path)
    assert r.cached_tokens is None


# --- (a) cold per cell next to the warm aggregates ------------------------------------------


def _cell_records(*, scenario: str, cold_start: bool) -> list[RunRecord]:
    """One cell as the runner writes it. For cell 0 the cold-start precedes the warmup, so the
    warmup is ALREADY warm; for every other cell the warmup is the first request."""
    out: list[RunRecord] = []
    if cold_start:
        out.append(_rec(run_id="cold-0", scenario=scenario, is_cold_start=True, ttft_s=7.3,
                        prefill_tps=550.0, cached_tokens=0))  # fmt: skip
        out.append(_rec(run_id="0-0", scenario=scenario, warmup=True, ttft_s=0.21,
                        prefill_tps=19000.0, cached_tokens=3968))  # fmt: skip
    else:
        out.append(_rec(run_id="1-0", scenario=scenario, warmup=True, ttft_s=6.9,
                        prefill_tps=580.0, cached_tokens=0))  # fmt: skip
    out += [
        _rec(run_id=f"x-{i}", scenario=scenario, ttft_s=t, prefill_tps=19000.0, cached_tokens=3968)
        for i, t in enumerate((0.20, 0.21, 0.22))
    ]
    return out


def test_cold_ttft_is_the_first_request_of_each_cell():  # type: ignore[no-untyped-def]
    records = _cell_records(scenario="bodydouble", cold_start=True) + _cell_records(
        scenario="rag_synth", cold_start=False
    )
    cells = {c.scenario: c for c in report.aggregate_cells(records)}
    # cell 0: the cold-start, NOT its (already warm) warmup
    assert cells["bodydouble"].ttft_cold == 7.3
    assert cells["bodydouble"].prefill_cold == 550.0
    # other cells: the warmup
    assert cells["rag_synth"].ttft_cold == 6.9
    # the warm aggregates are untouched — they still exclude warmup and cold-start
    for c in cells.values():
        assert abs(c.ttft_p50 - 0.21) < 1e-9
        assert c.n_valid == 3


def test_cached_share_is_reported_warm_and_cold():  # type: ignore[no-untyped-def]
    cells = {
        c.scenario: c
        for c in report.aggregate_cells(
            _cell_records(scenario="bodydouble", cold_start=True)
            + _cell_records(scenario="rag_synth", cold_start=False)
        )
    }
    assert cells["rag_synth"].cached_pct_warm == 3968 / 4000 * 100
    assert cells["rag_synth"].cached_pct_cold == 0.0


def test_cached_share_is_unknown_not_zero_without_the_field():  # type: ignore[no-untyped-def]
    """mlx_lm.server / ollama never send cached_tokens — the report must say 'unknown', not 0 %."""
    (cell,) = report.aggregate_cells([_rec(warmup=True, ttft_s=1.0), _rec(), _rec(), _rec()])
    assert cell.cached_pct_warm is None and cell.cached_pct_cold is None
    assert cell.ttft_cold == 1.0  # the cold column itself works without the field


def test_cold_ttft_is_nan_when_the_first_request_failed():  # type: ignore[no-untyped-def]
    (cell,) = report.aggregate_cells(
        [_rec(warmup=True, ok=False, error="boom", ttft_s=0.0), _rec(), _rec(), _rec()]
    )
    assert math.isnan(cell.ttft_cold)


def test_report_shows_cold_and_cache_columns():  # type: ignore[no-untyped-def]
    md = report.render_report_md(
        _cell_records(scenario="rag_synth", cold_start=False), date_str="2026-09-21"
    )
    assert "TTFT kalt (s)" in md and "Cache warm/kalt" in md
    row = next(ln for ln in md.splitlines() if ln.startswith("| 2026-09-21"))
    assert "| 6.90 |" in row  # cold TTFT
    assert "99 % / 0 %" in row  # 3968/4000 warm, 0 cold


def test_report_marks_unknown_cache_share_as_dash():  # type: ignore[no-untyped-def]
    md = report.render_report_md(
        [_rec(warmup=True, ttft_s=1.0), _rec(), _rec(), _rec()], date_str="2026-09-21"
    )
    row = next(ln for ln in md.splitlines() if ln.startswith("| 2026-09-21"))
    assert "| — |" in row and "%" not in row


def test_cold_value_of_the_runs_first_cell_is_marked_as_including_model_load():  # type: ignore[no-untyped-def]
    """Cell 0's first request is the cold-start of the whole run — it carries model loading, not
    just a cold cache. Found on real data: mlx_lm.server (no prefix cache) showed cold 6.42 s vs
    warm 0.32 s in cell 0 but 3.85 s vs 4.17 s in cell 1. The number is real, so it stays — but it
    must not read as comparable with the other cells' cold values."""
    records = _cell_records(scenario="bodydouble", cold_start=True) + _cell_records(
        scenario="rag_synth", cold_start=False
    )
    cells = {c.scenario: c for c in report.aggregate_cells(records)}
    assert cells["bodydouble"].cold_includes_load is True
    assert cells["rag_synth"].cold_includes_load is False
    md = report.render_report_md(records, date_str="2026-09-21")
    rows = [ln for ln in md.splitlines() if ln.startswith("| 2026-09-21")]
    (row0,) = [r for r in rows if "7.30" in r]
    (row1,) = [r for r in rows if "6.90" in r]
    assert "7.30 ⁽ᴸ⁾" in row0 and "⁽ᴸ⁾" not in row1
    assert "⁽ᴸ⁾ =" in md  # the legend explains the marker
