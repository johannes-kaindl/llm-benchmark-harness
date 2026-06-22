"""Config loading + validation.

The whole point of the harness is that swapping this YAML — and nothing else —
moves a run from an M1/LM-Studio box to an M5/mlx-lm box. So validation lives
here and is strict: a typo in a scenario name or an unreachable bucket should
fail before a single request is sent.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

# Built-in default output budget per scenario (tokens). Overridable via config.max_tokens.
DEFAULT_MAX_TOKENS: dict[str, int] = {
    "bodydouble": 150,
    "compose": 400,
    "rag_synth": 300,
    "longctx_stress": 128,
    "vlm": 300,
}

KNOWN_SCENARIOS = set(DEFAULT_MAX_TOKENS)


class Endpoint(BaseModel):
    base_url: str
    api_key: str = "not-needed"


class ModelSpec(BaseModel):
    id: str
    quant: str = ""
    max_tokens_default: int = 400


class EmbedSpec(BaseModel):
    model: str = "bge-m3"
    num_chunks: int = 200
    chunk_tokens: int = 512


class VlmSpec(BaseModel):
    image_path: str = "prompts/vlm_sample.png"


class Config(BaseModel):
    endpoint: Endpoint
    machine: str
    runs_per_cell: int = 8
    seed: int = 42
    temperature: float = 0.0
    context_buckets: list[int] = Field(default_factory=lambda: [4096, 16384, 32768])
    scenarios: list[str] = Field(
        default_factory=lambda: ["bodydouble", "compose", "rag_synth", "longctx_stress"]
    )
    models: list[ModelSpec]
    max_tokens: dict[str, int] = Field(default_factory=dict)
    server_process_match: str = ""
    output_dir: str = "./runs"
    power_check: bool = True
    # Optional engine identity for the report header. Auto-detected from the port
    # when empty; mlx_lm/mlx versions usually have to be set by hand because the
    # OpenAI-compatible API does not expose them.
    engine: str = ""
    engine_version: str = ""
    vlm: VlmSpec = Field(default_factory=VlmSpec)
    embed: EmbedSpec = Field(default_factory=EmbedSpec)

    @field_validator("scenarios")
    @classmethod
    def _known_scenarios(cls, v: list[str]) -> list[str]:
        unknown = set(v) - KNOWN_SCENARIOS
        if unknown:
            raise ValueError(
                f"unknown scenario(s): {sorted(unknown)}; known: {sorted(KNOWN_SCENARIOS)}"
            )
        return v

    @field_validator("models")
    @classmethod
    def _at_least_one_model(cls, v: list[ModelSpec]) -> list[ModelSpec]:
        if not v:
            raise ValueError("at least one model is required")
        return v

    @field_validator("runs_per_cell")
    @classmethod
    def _enough_runs(cls, v: int) -> int:
        if v < 2:
            raise ValueError("runs_per_cell must be >= 2 (one warmup is always discarded)")
        return v

    @field_validator("context_buckets")
    @classmethod
    def _valid_buckets(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("context_buckets must not be empty")
        if any(b <= 0 for b in v):
            raise ValueError(f"context_buckets must all be positive: {v}")
        return v

    def max_tokens_for(self, scenario: str, model: ModelSpec) -> int:
        """Per-scenario output budget: explicit override → scenario default → model default."""
        if scenario in self.max_tokens:
            return self.max_tokens[scenario]
        if scenario in DEFAULT_MAX_TOKENS:
            return DEFAULT_MAX_TOKENS[scenario]
        return model.max_tokens_default

    def output_path(self) -> Path:
        return Path(self.output_dir)


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config {path} did not parse to a mapping")
    return Config.model_validate(raw)


def models_from_json(s: str) -> list[ModelSpec]:
    """Parse a JSON array of model specs (GUI picker). Raises ValueError on bad JSON,
    a non-array, an empty array, or a spec with a missing/blank ``id``. De-duplicates exact
    (id, quant) duplicates. (Note: same id with differing quant stays distinct — the picker
    guards against re-adding a checked model by id so it can't double an eval cell.)"""
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid models JSON: {e}") from e
    if not isinstance(data, list) or not data:
        raise ValueError("models_json must be a non-empty JSON array")
    try:
        specs = [ModelSpec(**m) for m in data]
    except (ValidationError, TypeError) as e:
        raise ValueError(f"invalid model spec: {e}") from e
    if any(not m.id.strip() for m in specs):
        raise ValueError("model id must be non-empty")
    out: list[ModelSpec] = []
    seen: set[tuple[str, str]] = set()
    for m in specs:
        key = (m.id, m.quant)
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def apply_models_override(cfg: Config, models_json: str) -> Config:
    """Return cfg with its models replaced by ``models_json`` (the GUI picker selection).
    An empty/blank string means 'no override' and returns cfg unchanged (resume / CLI default)."""
    if not models_json.strip():
        return cfg
    return cfg.model_copy(update={"models": models_from_json(models_json)})
