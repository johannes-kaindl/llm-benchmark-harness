"""Merge the latency log (RunRecords) with the resource log (ResourceSamples).

Two decoupled producers, joined here by time window. For each run we take the
samples whose timestamp falls inside [t_start, t_end] (widened by one interval),
roll them up, and fill the record's resource fields. Short requests that span no
full sample tick fall back to the single nearest sample so a row is never blank.
"""

from __future__ import annotations

import json
from pathlib import Path

from ramcheck.models import (
    ResourceAggregate,
    ResourceSample,
    RunRecord,
    pressure_max,
)

DEFAULT_TOLERANCE_S = 0.5  # one sampler interval of slack on each side


def load_samples_jsonl(path: str | Path) -> list[ResourceSample]:
    samples: list[ResourceSample] = []
    p = Path(path)
    if not p.exists():
        return samples
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        samples.append(ResourceSample(**d))
    return samples


def _window_samples(
    samples: list[ResourceSample], t_start: float, t_end: float, tol: float
) -> list[ResourceSample]:
    inside = [s for s in samples if t_start - tol <= s.ts <= t_end + tol]
    if inside:
        return inside
    if not samples:
        return []
    mid = (t_start + t_end) / 2.0
    nearest = min(samples, key=lambda s: abs(s.ts - mid))
    return [nearest]


def aggregate_window(samples: list[ResourceSample]) -> ResourceAggregate:
    if not samples:
        return ResourceAggregate(
            peak_rss_mb=None,
            sys_used_mb=0.0,
            swap_delta_mb=0.0,
            mem_pressure_max="normal",
            throttled=False,
            n_samples=0,
        )
    rss = [s.server_rss_mb for s in samples if s.server_rss_mb is not None]
    swaps = [s.swap_used_mb for s in samples]
    return ResourceAggregate(
        peak_rss_mb=max(rss) if rss else None,
        sys_used_mb=max(s.sys_used_mb for s in samples),
        swap_delta_mb=max(swaps) - min(swaps),
        mem_pressure_max=pressure_max([s.mem_pressure_level for s in samples]),
        throttled=any(s.throttled for s in samples),
        n_samples=len(samples),
    )


def merge_run(
    record: RunRecord, samples: list[ResourceSample], tol: float = DEFAULT_TOLERANCE_S
) -> RunRecord:
    window = _window_samples(samples, record.t_start, record.t_end, tol)
    agg = aggregate_window(window)
    record.peak_rss_mb = agg.peak_rss_mb
    record.sys_used_mb = agg.sys_used_mb
    record.swap_delta_mb = agg.swap_delta_mb
    record.mem_pressure_max = agg.mem_pressure_max
    # A run is throttled if powermetrics flagged it OR it ran on battery is handled
    # separately (power_source); here only the thermal flag.
    record.throttled = record.throttled or agg.throttled
    return record


def merge(
    records: list[RunRecord], samples: list[ResourceSample], tol: float = DEFAULT_TOLERANCE_S
) -> list[RunRecord]:
    return [merge_run(r, samples, tol) for r in records]
