"""Pure helpers for the in-browser pack editor: validate pack YAML through the *one*
pydantic contract (``touchstone.pack``) and confine a save filename. No file I/O, never
raises — the routes stay thin over these. Mirrors the shape of ``configs.py``."""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import ValidationError

from touchstone.pack import Pack

# A minimal-but-valid pack: the starting point for "+ Neues Pack". Editing then leans on
# the live validator + preview, so this only has to pass Pack.model_validate.
NEW_PACK_TEMPLATE = """\
id: neues_pack
title: Neues Pack
version: 1
description: ""

# Die 1..5-Skala (Pflicht: genau die Schlüssel 1 bis 5).
scale:
  1: "untauglich"
  2: "schwach"
  3: "brauchbar"
  4: "gut"
  5: "exzellent"

# Gewichtete Querschnitts-Dimensionen (Q1..Qn). weight muss positiv sein.
dimensions:
  - {id: Q1, name: "Fachliche Qualität", weight: 2}
  - {id: Q2, name: "Sicherheit", weight: 1}

# Sicherheits-K.-o.: eine Dimension-Untergrenze + (optional) fatale red-flag-Prompts.
ko_rule:
  dimension: Q2
  threshold: 2
  red_flag_prompts: []

# Die System-Prompt-Achse (Prompt-Benchmarking). Mindestens eine Variante.
prompt_variants:
  - {id: none, system_prompt: null}

# Deterministisch per Default (reproduzierbar/vergleichbar).
sampling:
  temperature: 0.0
  seed: 42

# Die Test-Prompts, gruppiert in Kategorien.
categories:
  - id: A
    name: Allgemein
    prompts:
      - id: A1
        title: Beispiel-Prompt
        prompt: "Stelle hier deine Test-Frage."
        tests: "Worauf der Judge achten soll."
        green_flags: []
        red_flags: []
"""


def _format_errors(exc: ValidationError) -> list[dict[str, str]]:
    """pydantic ValidationError → [{loc: dotted-path, msg}]. The dotted loc lets the editor
    point at the offending field (e.g. ``dimensions.0.weight``)."""
    out: list[dict[str, str]] = []
    for e in exc.errors():
        loc = ".".join(str(p) for p in e.get("loc", ()))
        out.append({"loc": loc, "msg": str(e.get("msg", ""))})
    return out


def validate_pack_yaml(text: str) -> dict[str, Any]:
    """Parse + validate pack YAML against the pydantic contract. Pure, never raises.

    Returns ``{ok, errors, summary, pack}``: on success ``pack`` is the validated ``Pack``
    (so the route renders the preview without re-validating) and ``summary`` carries the
    counts; on any failure ``pack``/``summary`` are ``None`` and ``errors`` is non-empty.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return {
            "ok": False,
            "errors": [{"loc": "(yaml)", "msg": str(e)}],
            "summary": None,
            "pack": None,
        }
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "errors": [{"loc": "(root)", "msg": "Pack-YAML muss ein Mapping (Objekt) sein."}],
            "summary": None,
            "pack": None,
        }
    try:
        pack = Pack.model_validate(raw)
    except ValidationError as e:
        return {"ok": False, "errors": _format_errors(e), "summary": None, "pack": None}
    summary = {
        "prompts": len(pack.all_prompts()),
        "dimensions": len(pack.dimensions),
        "variants": len(pack.prompt_variants),
        "categories": len(pack.categories),
        "max_weighted": pack.max_weighted(),
    }
    return {"ok": True, "errors": [], "summary": summary, "pack": pack}


def safe_pack_filename(name: str) -> str | None:
    """Confine a save target to ``packs/<name>.yaml``. Returns the bare ``name.yaml`` or None.

    Accepts a stem of ``[A-Za-z0-9_-]+`` (with an optional ``.yaml`` suffix); rejects path
    separators, traversal, other extensions and empty stems — so it can never escape ``packs/``.
    """
    name = name.strip()
    if name.endswith(".yaml"):
        stem = name[:-5]
    elif "." in name:
        return None  # some other extension (.yml/.json/…) — only .yaml is written
    else:
        stem = name
    if not re.fullmatch(r"[A-Za-z0-9_-]+", stem):
        return None
    return stem + ".yaml"
