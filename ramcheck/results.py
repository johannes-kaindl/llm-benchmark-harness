"""Eval data contract — the records the qualitative pipeline speaks.

Parallel to ``models.py`` (which pins the *performance* CSV contract), this pins
the *qualitative* one: ``EvalResponse`` is one generated answer plus its perf
record (written by ``qualrun`` to ``responses.jsonl``); ``Verdict`` is one judged
answer; ``ModelReport`` holds the holistic master-dimension scores per
(model, variant). Generation fills ``EvalResponse``; judging fills the rest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class EvalResponse:
    """One generated answer + its per-request perf record."""

    # identity
    pack_id: str
    pack_version: int
    machine: str
    model: str
    quant: str
    engine: str
    engine_version: str
    variant: str
    category: str
    prompt_id: str
    repeat: int
    # the answer
    response_text: str
    content_empty: bool
    # latency (from the runner's metric derivation)
    ttft_s: float
    decode_tps: float
    prefill_tps: float
    e2e_s: float
    prompt_tokens: int
    completion_tokens: int
    is_cold_start: bool
    power_source: str
    # resources (filled by merge from the host sampler)
    peak_rss_mb: float | None
    sys_used_mb: float | None
    mem_pressure_max: str
    throttled: bool
    # bookkeeping
    ok: bool
    error: str
    seed: int
    t_start: float
    t_end: float
    reasoning_chars: int = 0  # length of "thinking" output (0 if none / non-reasoning model)
    reasoning_text: str = ""  # the "thinking" text — persisted ONLY when content_empty (else "")
    # baseline = pre-run host memory floor; delta = peak − baseline (cross-machine-comparable
    # model memory growth). Both None on bundles produced before the baseline tick existed.
    sys_used_baseline_mb: float | None = None
    sys_used_delta_mb: float | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Verdict:
    """One judged answer: a 1..5 score, a red-flag bool, and the judge's reason."""

    model: str
    variant: str
    prompt_id: str
    repeat: int
    category: str
    score: int
    red_flag: bool
    rationale: str
    unscored: bool = False  # judge unreachable / unparseable → excluded from means
    safety_critical: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ModelReport:
    """Holistic master-dimension scores for one (model, variant)."""

    model: str
    variant: str
    dim_scores: dict[str, int] = field(default_factory=dict)
    dim_rationales: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
