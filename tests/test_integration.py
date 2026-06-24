"""End-to-end pipeline with a fake client and no real host sampling.

Exercises iter_cells → stream_once → record building → merge → report, proving
the whole chain produces report.md + raw.csv with the right row counts.
"""

from touchstone import report
from touchstone.config import Config
from touchstone.runner import StreamEvent, run_benchmark


class FakeClient:
    engine = "fake"
    engine_version = "1.2.3"

    def stream(self, *, messages, model, max_tokens, temperature, seed):
        yield StreamEvent(delta_text="Hallo ")
        yield StreamEvent(delta_text="Welt")
        yield StreamEvent(prompt_tokens=123, completion_tokens=7)


class NoopSampler:
    def start(self):
        pass

    def stop(self):
        pass


def _cfg(tmp_path):
    return Config.model_validate(
        {
            "endpoint": {"base_url": "http://localhost:1234/v1"},
            "machine": "M1-test",
            "runs_per_cell": 3,
            "context_buckets": [4096],
            "scenarios": ["bodydouble", "rag_synth"],
            "models": [{"id": "qwen3-8b", "quant": "Q5"}],
            "power_check": False,
            "output_dir": str(tmp_path),
        }
    )


def test_run_benchmark_full_pipeline(tmp_path):
    cfg = _cfg(tmp_path)
    run_dir = tmp_path / "run1"
    records = run_benchmark(cfg, FakeClient(), run_dir=run_dir, sampler=NoopSampler())

    # cells: bodydouble (ctx 0) + rag_synth (ctx 4096) = 2 cells
    # per cell: 3 runs; plus 1 cold-start overall = 2*3 + 1 = 7 records
    assert len(records) == 7
    assert sum(1 for r in records if r.is_cold_start) == 1
    assert sum(1 for r in records if r.warmup) == 2  # one warmup per cell
    assert all(r.engine == "lm-studio" for r in records)
    assert all(r.engine_version == "1.2.3" for r in records)
    assert all(r.actual_prompt_tokens == 123 for r in records)

    # Write + read back the report artifacts.
    md_path, raw_path = report.write_report(records, run_dir, date_str="2026-06-15")
    assert md_path.exists() and raw_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert "Cold-Start" in md
    assert "M1-test" in md
    # engine resolved from the :1234 port, version taken from the client
    assert "lm-studio 1.2.3" in md

    reloaded = report.load_raw_csv(raw_path)
    assert len(reloaded) == 7


def test_run_benchmark_aggregates_exclude_warmup_and_cold(tmp_path, monkeypatch):
    # Make the test hermetic w.r.t. the host's power state: on battery the runner
    # would flag every run and aggregate_cells would exclude them (n_valid → 0).
    monkeypatch.setattr("touchstone.sampler.read_power_source", lambda: "ac")
    cfg = _cfg(tmp_path)
    records = run_benchmark(cfg, FakeClient(), run_dir=tmp_path / "run2", sampler=NoopSampler())
    cells = report.aggregate_cells(records)
    # 2 cells, each with 2 valid runs (3 runs − 1 warmup), cold excluded from cells
    assert len(cells) == 2
    assert all(c.n_valid == 2 for c in cells)
