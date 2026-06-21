"""Pure helpers to surface the models defined inside config*.yaml files for the
Konfig+Start model picker. Defensive: a broken/missing config yields no models so a
single bad file never breaks the page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ramcheck.config import ModelSpec


def config_models(path: str | Path) -> list[ModelSpec]:
    """The validated ``models:`` of a config file, or [] on any read/parse error.

    Parses only the models list (not the full Config) so a config with a placeholder
    endpoint still shows its models in the picker.
    """
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    items = raw.get("models")
    if not isinstance(items, list):
        return []
    out: list[ModelSpec] = []
    for m in items:
        if not isinstance(m, dict):
            continue
        try:
            out.append(ModelSpec(**m))
        except Exception:
            continue
    return out


def models_by_config(files: list[str]) -> dict[str, list[dict[str, Any]]]:
    """{config_path: [model.model_dump(), ...]} for embedding into the template (JSON)."""
    return {f: [m.model_dump() for m in config_models(f)] for f in files}
