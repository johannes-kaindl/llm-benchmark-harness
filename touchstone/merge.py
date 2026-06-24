"""Merge the latency log (RunRecords) with the resource log (ResourceSamples).

Two decoupled producers, joined here by time window. For each run we take the
samples whose timestamp falls inside [t_start, t_end] (widened by one interval),
roll them up, and fill the record's resource fields. Short requests that span no
full sample tick fall back to the single nearest sample so a row is never blank.
"""

from __future__ import annotations

import json
from pathlib import Path

from touchstone.models import (
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
        # Tolerant of a half-written final line (crashed/resumed bundle) — a truncated
        # tail must degrade only the sparkline, never collapse the result page.
        try:
            samples.append(ResourceSample(**json.loads(line)))
        except Exception:
            continue
    return samples


def baseline_sys_used_mb(samples: list[ResourceSample]) -> float | None:
    """The pre-run host memory floor: the ``baseline``-flagged tick's ``sys_used_mb``,
    or (when no tick is flagged — e.g. an older bundle) the ``min`` over all samples.
    None only when there are no samples at all."""
    if not samples:
        return None
    flagged = [s.sys_used_mb for s in samples if s.baseline]
    if flagged:
        return min(flagged)
    return min(s.sys_used_mb for s in samples)


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


def aggregate_window(
    samples: list[ResourceSample], baseline_mb: float | None = None
) -> ResourceAggregate:
    if not samples:
        return ResourceAggregate(
            peak_rss_mb=None,
            sys_used_mb=0.0,
            swap_delta_mb=0.0,
            mem_pressure_max="normal",
            throttled=False,
            n_samples=0,
            sys_used_baseline_mb=baseline_mb,
            sys_used_delta_mb=None,
        )
    rss = [s.server_rss_mb for s in samples if s.server_rss_mb is not None]
    swaps = [s.swap_used_mb for s in samples]
    peak_used = max(s.sys_used_mb for s in samples)
    delta = (peak_used - baseline_mb) if baseline_mb is not None else None
    return ResourceAggregate(
        peak_rss_mb=max(rss) if rss else None,
        sys_used_mb=peak_used,
        swap_delta_mb=max(swaps) - min(swaps),
        mem_pressure_max=pressure_max([s.mem_pressure_level for s in samples]),
        throttled=any(s.throttled for s in samples),
        n_samples=len(samples),
        sys_used_baseline_mb=baseline_mb,
        sys_used_delta_mb=delta,
    )


def resources_for_window(
    samples: list[ResourceSample], t_start: float, t_end: float, tol: float = DEFAULT_TOLERANCE_S
) -> ResourceAggregate:
    """Roll up the samples inside [t_start, t_end] (with slack). Public so the eval
    runner can fill an EvalResponse's resources without building a throwaway RunRecord.

    The baseline tick is captured before the first request — outside every run window — so
    we derive it from the *full* sample list, then subtract it from the in-window peak."""
    baseline = baseline_sys_used_mb(samples)
    return aggregate_window(_window_samples(samples, t_start, t_end, tol), baseline)


def merge_run(
    record: RunRecord, samples: list[ResourceSample], tol: float = DEFAULT_TOLERANCE_S
) -> RunRecord:
    agg = resources_for_window(samples, record.t_start, record.t_end, tol)
    record.peak_rss_mb = agg.peak_rss_mb
    record.sys_used_mb = agg.sys_used_mb
    record.sys_used_delta_mb = agg.sys_used_delta_mb
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
