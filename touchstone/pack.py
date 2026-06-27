"""Use-case **pack**: the data file that defines a qualitative evaluation.

A pack is *data, not code* — swapping the YAML moves the harness from a
Neurodivergenz-Assistent test to an Office-Assistent test (or any use case) with
no code change. Validation lives here and is strict, mirroring ``config.py``: a
dangling dimension reference or a duplicate prompt id fails before a single
request is sent.

A pack bundles everything the brief specifies for one use case:
  * ``scale``           — the 1..5 meaning, rendered into the scorecard legend
  * ``dimensions``      — the weighted cross-cutting master dimensions (Q1..Qn)
  * ``ko_rule``         — the safety knock-out (a dimension floor + fatal red-flag prompts)
  * ``prompt_variants`` — the system-prompt axis (enables prompt-benchmarking)
  * ``sampling``        — deterministic by default (temp 0 + seed), overridable per pack
  * ``categories[]``    — the prompts, each with green/red flags and a tested focus
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class Dimension(BaseModel):
    """One weighted cross-cutting quality dimension (e.g. Q1 Fachliche Korrektheit, ×3)."""

    id: str
    name: str
    weight: int = 1
    about: str = ""

    @field_validator("weight")
    @classmethod
    def _positive_weight(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"dimension weight must be positive: {v}")
        return v


class KoRule(BaseModel):
    """Safety knock-out: a dimension floor plus red-flagged prompts.

    ``red_flag_scope`` controls which red flags are fatal:
      * ``"all"`` (default): ANY judge red flag knocks out — conservative, right for a
        safety pack (ndassist). Legacy behaviour; packs without the field keep it.
      * ``"curated"``: only a red flag on a prompt in ``red_flag_prompts`` knocks out;
        other red flags lower the score but don't disqualify — right for a quality pack
        (buero), where hallucination must disqualify but a tone/format slip must not.
    The dimension floor applies in both scopes.
    """

    dimension: str
    threshold: int = 2
    red_flag_scope: Literal["all", "curated"] = "all"
    red_flag_prompts: list[str] = Field(default_factory=list)


class PromptVariant(BaseModel):
    """One axis value of the prompt-benchmarking dimension (a system prompt or none)."""

    id: str
    system_prompt: str | None = None


class Sampling(BaseModel):
    """Generation knobs. Deterministic by default so runs are reproducible/comparable."""

    temperature: float = 0.0
    seed: int = 42


class PackPrompt(BaseModel):
    """One test prompt with its rubric (green/red flags) and per-prompt knobs."""

    id: str
    title: str
    prompt: str
    tests: str = ""
    max_tokens: int | None = (
        None  # None = no limit (answer freely, like real use); a positive int caps it
    )
    repeats: int = 1
    green_flags: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    safety_critical: bool = False  # a red flag here feeds the K.-o. rule
    format_strict: bool = False  # judge weights literal format adherence

    @field_validator("max_tokens")
    @classmethod
    def _positive_max_tokens(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError(f"max_tokens must be positive or null (null = no limit): {v}")
        return v

    @field_validator("repeats")
    @classmethod
    def _repeats_at_least_one(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"repeats must be >= 1: {v}")
        return v


class Category(BaseModel):
    id: str
    name: str
    prompts: list[PackPrompt]


class Pack(BaseModel):
    id: str
    title: str
    version: int = 1
    description: str = ""
    scale: dict[int, str]
    dimensions: list[Dimension]
    ko_rule: KoRule
    prompt_variants: list[PromptVariant] = Field(
        default_factory=lambda: [PromptVariant(id="none", system_prompt=None)]
    )
    sampling: Sampling = Field(default_factory=Sampling)
    categories: list[Category]

    @field_validator("scale")
    @classmethod
    def _scale_one_to_five(cls, v: dict[int, str]) -> dict[int, str]:
        if set(v) != {1, 2, 3, 4, 5}:
            raise ValueError(f"scale keys must be exactly 1..5, got {sorted(v)}")
        return v

    @field_validator("prompt_variants")
    @classmethod
    def _at_least_one_variant(cls, v: list[PromptVariant]) -> list[PromptVariant]:
        if not v:
            raise ValueError("at least one prompt_variant is required")
        return v

    @model_validator(mode="after")
    def _cross_references(self) -> Pack:
        prompt_ids = [p.id for _, p in self.all_prompts()]
        dupes = {pid for pid in prompt_ids if prompt_ids.count(pid) > 1}
        if dupes:
            raise ValueError(f"duplicate prompt ids: {sorted(dupes)}")

        dim_ids = {d.id for d in self.dimensions}
        if self.ko_rule.dimension not in dim_ids:
            raise ValueError(
                f"ko_rule.dimension '{self.ko_rule.dimension}' is not a known dimension {sorted(dim_ids)}"
            )

        missing = set(self.ko_rule.red_flag_prompts) - set(prompt_ids)
        if missing:
            raise ValueError(
                f"ko_rule.red_flag_prompts reference unknown prompts: {sorted(missing)}"
            )

        variant_ids = [v.id for v in self.prompt_variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError(f"duplicate prompt_variant ids: {variant_ids}")
        return self

    def all_prompts(self) -> list[tuple[Category, PackPrompt]]:
        """Every (category, prompt) in declaration order."""
        return [(c, p) for c in self.categories for p in c.prompts]

    @property
    def max_weighted(self) -> int:
        """Maximum achievable weighted score = 5 × sum(weights)."""
        return 5 * sum(d.weight for d in self.dimensions)


def load_pack(path: str | Path) -> Pack:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"pack {path} did not parse to a mapping")
    return Pack.model_validate(raw)
