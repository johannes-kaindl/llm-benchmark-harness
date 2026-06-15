"""Shared data contract.

Every module in the harness speaks these types. ``RunRecord`` is the single
source of truth for one raw.csv row; ``RAW_CSV_COLUMNS`` pins the column order so
runner/sampler/merge/report cannot drift apart. ``ResourceSample`` is one 2 Hz
host-sampler tick; ``ResourceAggregate`` is what merging a run's time-window of
samples produces.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

# Memory-pressure levels ordered low → high so "max over a run" is a plain max().
PRESSURE_ORDER: dict[str, int] = {"normal": 0, "warn": 1, "critical": 2}


def pressure_max(levels: list[str]) -> str:
    """Return the most severe memory-pressure level seen, or "normal" if empty."""
    if not levels:
        return "normal"
    return max(levels, key=lambda lvl: PRESSURE_ORDER.get(lvl, 0))


@dataclass
class ResourceSample:
    """One host-sampler tick (~2 Hz). Engine-agnostic; the source of truth for memory."""

    ts: float  # epoch seconds
    sys_used_mb: float
    sys_available_mb: float
    swap_used_mb: float
    server_rss_mb: float | None  # None when no server PID matched
    mem_pressure_level: str  # normal | warn | critical
    throttled: bool


@dataclass
class ResourceAggregate:
    """Per-run rollup of the resource samples that fall inside the run's window."""

    peak_rss_mb: float | None
    sys_used_mb: float  # peak system "used" over the window
    swap_delta_mb: float  # max(swap) - min(swap) over the window
    mem_pressure_max: str
    throttled: bool
    n_samples: int


@dataclass
class RunRecord:
    """One request. Latency fields are filled by the runner; resource fields by merge."""

    # --- identity / config (filled by runner) ---
    run_id: str
    machine: str
    model: str
    quant: str
    engine: str
    engine_version: str
    scenario: str
    target_ctx: int
    seed: int
    power_source: str  # ac | battery | unknown

    # --- latency (filled by runner) ---
    actual_prompt_tokens: int
    completion_tokens: int
    ttft_s: float
    decode_tps: float
    prefill_tps: float
    e2e_s: float

    # --- timing window for the merge join (epoch seconds) ---
    t_start: float
    t_end: float

    # --- bookkeeping ---
    warmup: bool = False
    is_cold_start: bool = False
    ok: bool = True
    error: str = ""

    # --- resource fields (filled by merge from the sampler log) ---
    peak_rss_mb: float | None = None
    sys_used_mb: float | None = None
    swap_delta_mb: float | None = None
    mem_pressure_max: str = ""
    throttled: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


# raw.csv column order — exactly the brief's spec, in this order.
RAW_CSV_COLUMNS: list[str] = [
    "run_id",
    "machine",
    "model",
    "quant",
    "engine",
    "engine_version",
    "scenario",
    "target_ctx",
    "actual_prompt_tokens",
    "completion_tokens",
    "ttft_s",
    "decode_tps",
    "prefill_tps",
    "e2e_s",
    "peak_rss_mb",
    "sys_used_mb",
    "swap_delta_mb",
    "mem_pressure_max",
    "throttled",
    "power_source",
    "seed",
    # extra bookkeeping columns kept so raw stays self-describing (brief: "roh behalten")
    "warmup",
    "is_cold_start",
    "ok",
    "error",
]


def _record_field_names() -> set[str]:
    return {f.name for f in fields(RunRecord)}


# Fail loud at import time if the CSV schema and the dataclass diverge.
_missing = set(RAW_CSV_COLUMNS) - _record_field_names()
if _missing:  # pragma: no cover - guards a developer mistake
    raise RuntimeError(f"RAW_CSV_COLUMNS references unknown RunRecord fields: {_missing}")


@dataclass
class EmbedResult:
    """Outcome of an `embed` run."""

    machine: str
    model: str
    engine: str
    engine_version: str
    num_chunks: int
    chunk_tokens: int
    total_s: float
    embeddings_per_s: float
    peak_rss_mb: float | None
    sys_used_mb: float | None
    power_source: str = "unknown"
    throttled: bool = False
    seed: int = 0
    extra: dict[str, object] = field(default_factory=dict)
