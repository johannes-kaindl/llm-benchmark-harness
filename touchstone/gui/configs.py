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


def config_summaries(files: list[str]) -> dict[str, dict[str, str]]:
    """{config_path: {"machine", "engine", "base_url"}} for an at-a-glance picker preview.

    Parses only those fields (NEVER the api_key) and tolerates malformed files (skipped),
    so the picker can show which endpoint/machine a config targets BEFORE you start a run —
    no more choosing a config blind.
    """
    out: dict[str, dict[str, str]] = {}
    for f in files:
        try:
            raw = yaml.safe_load(Path(f).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(raw, dict):
            continue
        endpoint = raw.get("endpoint")
        base_url = endpoint.get("base_url", "") if isinstance(endpoint, dict) else ""
        out[f] = {
            "machine": str(raw.get("machine", "") or ""),
            "engine": str(raw.get("engine", "") or ""),
            "base_url": str(base_url or ""),
        }
    return out


def order_configs(paths: list[str]) -> list[str]:
    """Sort config paths so *embed*/*vlm* files sort last (the picker shouldn't default to
    the embedding config). Within each group, alphabetical."""

    def key(p: str) -> tuple[bool, str]:
        name = Path(p).name.lower()
        return (("embed" in name or "vlm" in name), p)

    return sorted(paths, key=key)


def eval_model_options(
    *, config_models: list[dict[str, Any]], endpoint_models: list[str]
) -> dict[str, Any]:
    """Merge the config's declared models with the endpoint's actually-served ids into one
    single-select option list plus a sensible default. Pure; no I/O.

    The endpoint is the truth for *what can run now*; the config carries the thinking knobs.
    A served id that is also declared keeps the config knobs (``source="both"``) so a fair
    compare survives. Declared-but-not-served models are appended, flagged ``served=False`` —
    visible but never the default. Offline (no endpoint models) falls back to the config list.
    """
    by_id = {m["id"]: m for m in config_models if isinstance(m.get("id"), str)}

    def _opt(spec: dict[str, Any], *, served: bool, source: str) -> dict[str, Any]:
        return {
            "id": spec["id"],
            "quant": spec.get("quant", ""),
            "max_tokens_default": spec.get("max_tokens_default", 400),
            "reasoning_headroom_tokens": spec.get("reasoning_headroom_tokens", 0),
            "extra_body": spec.get("extra_body", {}),
            "served": served,
            "source": source,
        }

    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mid in endpoint_models:
        if not isinstance(mid, str) or mid in seen:
            continue
        seen.add(mid)
        if mid in by_id:
            options.append(_opt(by_id[mid], served=True, source="both"))
        else:
            options.append(_opt({"id": mid}, served=True, source="endpoint"))
    for m in config_models:
        cid = m.get("id")
        if not isinstance(cid, str) or cid in seen:
            continue
        seen.add(cid)
        options.append(_opt(m, served=False, source="config"))

    default_id = options[0]["id"] if options else None
    return {"options": options, "default_id": default_id}


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
