# tests/test_gui_cpu_sample.py
import touchstone.sampler as sampler_mod
from touchstone.merge import load_samples_jsonl
from touchstone.models import ResourceSample
from touchstone.sampler import HostSampler


def test_resource_sample_has_cpu_pct_defaulting_none():
    s = ResourceSample(
        ts=1.0,
        sys_used_mb=1.0,
        sys_available_mb=1.0,
        swap_used_mb=0.0,
        server_rss_mb=None,
        mem_pressure_level="normal",
        throttled=False,
    )
    assert s.cpu_pct is None  # last field, defaulted → old code constructs unchanged


def test_old_resources_jsonl_loads_without_cpu(tmp_path):
    p = tmp_path / "resources.jsonl"
    p.write_text(
        '{"ts":1.0,"sys_used_mb":1.0,"sys_available_mb":1.0,"swap_used_mb":0.0,'
        '"server_rss_mb":null,"mem_pressure_level":"normal","throttled":false}\n',
        encoding="utf-8",
    )
    samples = load_samples_jsonl(p)
    assert samples[0].cpu_pct is None  # missing key → default


def test_load_samples_jsonl_tolerates_half_line(tmp_path):
    """MAJOR 4: a truncated final line (crashed/resumed bundle) is skipped, not fatal."""
    p = tmp_path / "resources.jsonl"
    good = (
        '{"ts":1.0,"sys_used_mb":1.0,"sys_available_mb":1.0,"swap_used_mb":0.0,'
        '"server_rss_mb":null,"mem_pressure_level":"normal","throttled":false,"cpu_pct":42.0}'
    )
    p.write_text(good + "\n" + '{"ts":2.0,"sys_used_mb":', encoding="utf-8")
    samples = load_samples_jsonl(p)
    assert len(samples) == 1
    assert samples[0].cpu_pct == 42.0


def test_host_sampler_cpu_pct_primed_and_sampled(monkeypatch):
    """MAJOR 5: start() consumes the prime cpu_percent call; sample_once reports the real %.

    Injects a cpu_percent stub returning [0.0 (prime, discarded), 42.0]; makes the rest of
    sample_once hermetic so the asserted value is solely the sampler's cpu handling.
    """
    cpu_values = iter([0.0, 42.0])
    primed: list[bool] = []

    def fake_cpu_percent(interval=None):
        v = next(cpu_values)
        if not primed:  # first ever call is the prime in start()
            primed.append(True)
        return v

    class _VM:
        used = 0
        available = 0

    class _SW:
        used = 0

    monkeypatch.setattr(sampler_mod.psutil, "cpu_percent", fake_cpu_percent)
    monkeypatch.setattr(sampler_mod.psutil, "virtual_memory", lambda: _VM())
    monkeypatch.setattr(sampler_mod.psutil, "swap_memory", lambda: _SW())
    monkeypatch.setattr(sampler_mod, "server_rss_mb", lambda match: None)

    s = HostSampler(powermetrics=False)
    s.start()
    assert primed == [True]  # the prime call happened in start()
    sample = s.sample_once()
    assert sample.cpu_pct == 42.0  # the prime 0.0 was discarded
