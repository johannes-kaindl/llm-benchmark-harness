import json
import threading

from ramcheck.models import ResourceSample
from ramcheck.sampler import HostSampler


class _StubSampler(HostSampler):
    """HostSampler whose sample_once returns ticks with increasing sys_used_mb."""

    def __init__(self) -> None:
        super().__init__(powermetrics=False)
        self._n = 0

    def start(self) -> None:  # avoid touching psutil/powermetrics
        pass

    def stop(self) -> None:
        pass

    def sample_once(self) -> ResourceSample:
        self._n += 1
        return ResourceSample(
            ts=float(self._n),
            sys_used_mb=1000.0 + self._n,  # increasing each tick
            sys_available_mb=500.0,
            swap_used_mb=0.0,
            server_rss_mb=None,
            mem_pressure_level="normal",
            throttled=False,
            cpu_pct=10.0,
        )


def test_first_written_sample_is_flagged_baseline(tmp_path):
    sampler = _StubSampler()
    out = tmp_path / "resources.jsonl"
    stop = threading.Event()

    # Stop after a couple of loop ticks so we also write some non-baseline samples.
    ticks = {"n": 0}

    def wait(timeout=None):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            stop.set()
        return False

    stop.wait = wait  # type: ignore[method-assign]

    sampler.run_to_file(out, stop)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    records = [json.loads(line) for line in lines]

    # FIRST line carries the baseline marker.
    assert records[0]["baseline"] is True
    # Subsequent lines are not baseline.
    assert all(rec["baseline"] is False for rec in records[1:])
