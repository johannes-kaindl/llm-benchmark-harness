"""Nacht-Queue / Daisy-Chain orchestration: run several models overnight, sequentially.

Per entry: reset endpoint -> wait for RAM to settle -> ``eval`` (subprocess) -> optional
``judge`` (subprocess) -> record. One model per entry (fresh RAM baseline). Pure logic here
(schema, settle-wait, argv builders, summary, resume, step classification) with the real
subprocess/psutil/time wiring injected by the ``queue`` CLI command — so it stays testable
without a live server or sudo, like ``stream_once(clock=…)`` elsewhere in the repo.

Named ``runqueue`` (not ``queue``) so it never shadows the stdlib ``queue`` module.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .config import ModelSpec


# ----------------------------------------------------------------------- schema
class SettleSpec(BaseModel):
    timeout_s: float = 120
    plateau_polls: int = 3
    poll_interval_s: float = 2
    epsilon_mb: float = 200


class StepTimeouts(BaseModel):
    eval_s: float = Field(default=14400, alias="eval")
    judge_s: float = Field(default=21600, alias="judge")
    model_config = {"populate_by_name": True}


class QueueDefaults(BaseModel):
    reset_command: str = "lms unload --all"
    settle: SettleSpec = Field(default_factory=SettleSpec)
    step_timeout_s: StepTimeouts = Field(default_factory=StepTimeouts)
    cooldown_s: float = 0


class QueueEntry(BaseModel):
    config: Path
    pack: Path
    model: ModelSpec
    judge_config: Path | None = None
    judge_model: str = ""
    # optional per-entry overrides of the matching ``defaults`` key
    reset_command: str | None = None
    settle: SettleSpec | None = None
    step_timeout_s: StepTimeouts | None = None
    cooldown_s: float | None = None

    @field_validator("model", mode="before")
    @classmethod
    def _coerce_model(cls, v: object) -> object:
        return {"id": v} if isinstance(v, str) else v


class QueueSpec(BaseModel):
    defaults: QueueDefaults = Field(default_factory=QueueDefaults)
    entries: list[QueueEntry] = Field(default_factory=list)


def load_queue(path: str | Path) -> QueueSpec:
    """Parse + validate a queue.yaml. Raises ``ValueError`` on bad YAML, no entries, or a
    referenced ``config``/``pack``/``judge_config`` file that does not exist."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        spec = QueueSpec.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"invalid queue.yaml: {e}") from e
    if not spec.entries:
        raise ValueError("queue.yaml has no entries")
    for i, entry in enumerate(spec.entries):
        if not entry.config.exists():
            raise ValueError(f"entry {i}: config not found: {entry.config}")
        if not entry.pack.exists():
            raise ValueError(f"entry {i}: pack not found: {entry.pack}")
        if entry.judge_config is not None and not entry.judge_config.exists():
            raise ValueError(f"entry {i}: judge_config not found: {entry.judge_config}")
    return spec


# ----------------------------------------------------------------- run-dir slug
def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")


def run_dir_for(ts: str, model_id: str, pack_id: str) -> str:
    """Deterministic bundle dir name (timestamp + sanitized model id + pack id)."""
    return f"{ts}_{_slug(model_id)}_eval_{_slug(pack_id)}"


# ----------------------------------------------------------------- settle-wait
@dataclass
class SettleOutcome:
    settled: bool  # True = RAM plateau reached; False = timed out
    waited_s: float
    final_mb: float
    polls: int


def wait_until_settled(
    ram_poll: Callable[[], float],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    *,
    settle: SettleSpec,
) -> SettleOutcome:
    """Poll system RAM until it plateaus (``|Δ| < epsilon_mb`` for ``plateau_polls`` polls in
    a row) or ``timeout_s`` elapses. Condition-based, not a fixed sleep — so a freshly unloaded
    endpoint's baseline tick lands on the settled (low) state. ``sleep`` advances ``clock`` in
    tests."""
    start = clock()
    prev = ram_poll()
    polls = 1
    stable = 0
    while True:
        if clock() - start >= settle.timeout_s:
            return SettleOutcome(False, clock() - start, prev, polls)
        sleep(settle.poll_interval_s)
        cur = ram_poll()
        polls += 1
        if abs(cur - prev) < settle.epsilon_mb:
            stable += 1
            if stable >= settle.plateau_polls:
                return SettleOutcome(True, clock() - start, cur, polls)
        else:
            stable = 0
        prev = cur
