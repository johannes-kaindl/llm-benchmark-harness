"""Pure helpers to surface the models defined inside config*.yaml files for the
Konfig+Start model picker. Defensive: a broken/missing config yields no models so a
single bad file never breaks the page."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from touchstone.config import ModelSpec, load_config


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
        except (ValidationError, TypeError):
            continue
    return out


def models_by_config(files: list[str]) -> dict[str, list[dict[str, Any]]]:
    """{config_path: [model.model_dump(), ...]} for embedding into the template (JSON)."""
    return {f: [m.model_dump() for m in config_models(f)] for f in files}


def order_configs(paths: list[str]) -> list[str]:
    """Sort config paths so *embed*/*vlm* files sort last (the picker shouldn't default to
    the embedding config). Within each group, alphabetical."""

    def key(p: str) -> tuple[bool, str]:
        name = Path(p).name.lower()
        return (("embed" in name or "vlm" in name), p)

    return sorted(paths, key=key)


def discover_models(
    base_url: str,
    api_key: str,
    *,
    lister: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    """{"models": [...], "error": str|None}. NEVER raises. Shared by eval + judge discovery."""
    if lister is None:

        def lister() -> list[str]:
            from touchstone.client import OpenAIStreamClient

            client = OpenAIStreamClient(base_url, api_key, timeout=3.0, max_retries=0)
            return client.list_models()

    try:
        seen: set[str] = set()
        out: list[str] = []
        for m in lister():
            if m not in seen:
                seen.add(m)
                out.append(m)
    except Exception as e:
        return {"models": [], "error": f"Endpoint nicht erreichbar: {e}"}
    return {"models": out, "error": None}


def discover_endpoint_models(
    config_path: str | Path,
    *,
    lister: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    """Eval-config variant: load the config, discover its endpoint's models."""
    if lister is None:
        try:
            cfg = load_config(config_path)
        except Exception as e:
            return {"models": [], "error": f"Config nicht lesbar: {e}"}
        return discover_models(cfg.endpoint.base_url, cfg.endpoint.api_key)
    return discover_models("", "", lister=lister)


def discover_judge_endpoint_models(
    judge_config_path: str | Path,
    *,
    lister: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    """Judge-config variant: load the JudgeConfig, discover its endpoint's models."""
    if lister is None:
        from touchstone.judge import load_judge_config

        try:
            jc = load_judge_config(judge_config_path)
        except Exception as e:
            return {"models": [], "error": f"Judge-Config nicht lesbar: {e}"}
        return discover_models(jc.endpoint.base_url, jc.endpoint.api_key)
    return discover_models("", "", lister=lister)
