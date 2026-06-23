from __future__ import annotations

import pytest

from ramcheck.config import apply_overrides, load_config


def test_apply_overrides_replaces_whitelisted_keys():
    cfg = load_config("config.m5.yaml")
    out = apply_overrides(cfg, {"runs_per_cell": 4, "seed": 7})
    assert out.runs_per_cell == 4
    assert out.seed == 7
    # everything else intact
    assert out.endpoint == cfg.endpoint
    assert out.models == cfg.models
    assert out.temperature == cfg.temperature
    # original untouched (returns a copy)
    assert cfg.runs_per_cell != 4 or cfg.seed != 7


def test_apply_overrides_temperature():
    cfg = load_config("config.m5.yaml")
    out = apply_overrides(cfg, {"temperature": 0.7})
    assert out.temperature == 0.7


def test_apply_overrides_unknown_key_raises():
    cfg = load_config("config.m5.yaml")
    with pytest.raises(ValueError):
        apply_overrides(cfg, {"endpoint": {"base_url": "http://x"}})


def test_apply_overrides_empty_is_noop():
    cfg = load_config("config.m5.yaml")
    out = apply_overrides(cfg, {})
    assert out == cfg
