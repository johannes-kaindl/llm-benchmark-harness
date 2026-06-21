from __future__ import annotations

import pytest

from ramcheck.config import ModelSpec, apply_models_override, load_config, models_from_json


def test_models_from_json_valid():
    specs = models_from_json('[{"id":"a","quant":"Q4"},{"id":"b"}]')
    assert specs == [ModelSpec(id="a", quant="Q4"), ModelSpec(id="b")]
    assert specs[1].max_tokens_default == 400  # default applied


def test_models_from_json_rejects_empty_array():
    with pytest.raises(ValueError):
        models_from_json("[]")


def test_models_from_json_rejects_bad_json():
    with pytest.raises(ValueError):
        models_from_json("{not json")


def test_models_from_json_rejects_missing_id():
    with pytest.raises(ValueError):
        models_from_json('[{"quant":"Q4"}]')


def test_models_from_json_rejects_non_array():
    with pytest.raises(ValueError):
        models_from_json('{"id":"a"}')


def test_apply_models_override_empty_returns_same_config():
    cfg = load_config("config.m5.yaml")
    assert apply_models_override(cfg, "") is cfg
    assert apply_models_override(cfg, "   ") is cfg


def test_apply_models_override_replaces_models():
    cfg = load_config("config.m5.yaml")
    out = apply_models_override(cfg, '[{"id":"x","quant":"Q2"}]')
    assert [m.id for m in out.models] == ["x"]
    assert out.endpoint == cfg.endpoint  # everything else preserved


# ── Review fixes ──────────────────────────────────────────────────────────────


def test_models_from_json_rejects_blank_id():
    with pytest.raises(ValueError):
        models_from_json('[{"id":""}]')
    with pytest.raises(ValueError):
        models_from_json('[{"id":"   "}]')


def test_models_from_json_dedupes_by_id_quant():
    specs = models_from_json(
        '[{"id":"a","quant":"Q4"},{"id":"a","quant":"Q4"},{"id":"a","quant":"Q2"}]'
    )
    assert [(m.id, m.quant) for m in specs] == [("a", "Q4"), ("a", "Q2")]
