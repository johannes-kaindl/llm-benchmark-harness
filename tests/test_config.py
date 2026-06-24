import pytest
from pydantic import ValidationError

from touchstone.config import Config, load_config


def _base(**kw):
    d = {
        "endpoint": {"base_url": "http://localhost:1234/v1"},
        "machine": "M1-16GB",
        "models": [{"id": "qwen3-8b", "quant": "Q5_K_M"}],
    }
    d.update(kw)
    return d


def test_load_valid_config_file(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "endpoint:\n  base_url: http://localhost:1234/v1\n"
        "machine: M1\nmodels:\n  - {id: qwen3-8b, quant: Q5}\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.machine == "M1"
    assert cfg.endpoint.api_key == "not-needed"
    assert cfg.models[0].id == "qwen3-8b"


def test_unknown_scenario_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate(_base(scenarios=["bodydouble", "bogus"]))


def test_empty_models_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate(_base(models=[]))


def test_runs_per_cell_minimum():
    with pytest.raises(ValidationError):
        Config.model_validate(_base(runs_per_cell=1))


def test_context_buckets_must_not_be_empty():
    with pytest.raises(ValidationError):
        Config.model_validate(_base(context_buckets=[]))


def test_context_buckets_must_be_positive():
    with pytest.raises(ValidationError):
        Config.model_validate(_base(context_buckets=[4096, 0, -5]))


def test_max_tokens_resolution_order():
    cfg = Config.model_validate(_base(max_tokens={"bodydouble": 99}))
    model = cfg.models[0]
    assert cfg.max_tokens_for("bodydouble", model) == 99  # explicit override
    assert cfg.max_tokens_for("compose", model) == 400  # scenario default
    # scenario with no default falls back to model default
    model.max_tokens_default = 555
    assert cfg.max_tokens_for("unlisted", model) == 555


def test_example_configs_parse():
    # The shipped configs must validate.
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ["config.example.yaml", "config.m1.yaml", "config.m5.yaml"]:
        cfg = load_config(root / name)
        assert cfg.machine


def test_modelspec_thinking_defaults_are_neutral():
    from touchstone.config import ModelSpec

    m = ModelSpec(id="x")
    assert m.reasoning_headroom_tokens == 0
    assert m.extra_body == {}


def test_modelspec_thinking_fields_roundtrip_through_models_json():
    from touchstone.config import models_from_json

    specs = models_from_json(
        '[{"id": "gemma", "reasoning_headroom_tokens": 2000,'
        ' "extra_body": {"chat_template_kwargs": {"enable_thinking": false}}}]'
    )
    assert specs[0].reasoning_headroom_tokens == 2000
    assert specs[0].extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
