# tests/test_gui_cpu_sample.py
from ramcheck.merge import load_samples_jsonl
from ramcheck.models import ResourceSample


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
