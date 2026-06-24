"""Distribution statistics. Pure, dependency-free, unit-tested.

The brief is explicit: report distribution, not the mean. TTFT as P50 + P95,
decode/prefill as median, and TTFT consistency as CV% (stdev/mean * 100).
"""

from __future__ import annotations

import math
from statistics import median as _median


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (numpy default / "type 7").

    pct is in [0, 100]. Returns nan for an empty input so callers can decide how
    to render a missing cell rather than crashing the whole report.
    """
    if not values:
        return math.nan
    if len(values) == 1:
        return values[0]
    if not 0.0 <= pct <= 100.0:
        raise ValueError(f"percentile pct out of range: {pct}")
    ordered = sorted(values)
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[int(rank)]
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def median(values: list[float]) -> float:
    if not values:
        return math.nan
    return float(_median(values))


def cv_percent(values: list[float]) -> float:
    """Coefficient of variation in percent = stdev / mean * 100.

    Uses the sample stdev (n-1). Returns nan for <2 samples or a ~zero mean.
    """
    if len(values) < 2:
        return math.nan
    mean = sum(values) / len(values)
    if abs(mean) < 1e-12:
        return math.nan
    var = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(var) / mean * 100.0


def mean(values: list[float]) -> float:
    if not values:
        return math.nan
    return sum(values) / len(values)
