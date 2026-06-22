from __future__ import annotations

from ramcheck.gui import configs


def _write(p, text):
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_config_models_parses_models(tmp_path):
    c = _write(
        tmp_path / "config.x.yaml",
        "endpoint: {base_url: 'http://x/v1'}\nmachine: m\n"
        "models:\n  - {id: 'a', quant: 'Q4'}\n  - {id: 'b'}\n",
    )
    specs = configs.config_models(c)
    assert [m.id for m in specs] == ["a", "b"]
    assert specs[0].quant == "Q4"
    assert specs[1].max_tokens_default == 400


def test_config_models_defensive_on_missing_file(tmp_path):
    assert configs.config_models(tmp_path / "nope.yaml") == []


def test_config_models_defensive_on_broken_yaml(tmp_path):
    c = _write(tmp_path / "bad.yaml", "models: [unterminated\n")
    assert configs.config_models(c) == []


def test_config_models_defensive_on_no_models_key(tmp_path):
    c = _write(tmp_path / "nomodels.yaml", "machine: m\n")
    assert configs.config_models(c) == []


def test_models_by_config_maps_and_survives_one_broken(tmp_path):
    good = _write(tmp_path / "config.good.yaml", "models:\n  - {id: 'g'}\n")
    bad = _write(tmp_path / "config.bad.yaml", "models: [oops\n")
    out = configs.models_by_config([good, bad])
    assert out[good] == [
        {
            "id": "g",
            "quant": "",
            "max_tokens_default": 400,
            "reasoning_headroom_tokens": 0,
            "extra_body": {},
        }
    ]
    assert out[bad] == []


def test_discover_endpoint_models_success():
    out = configs.discover_endpoint_models("config.m5.yaml", lister=lambda: ["m1", "m2"])
    assert out == {"models": ["m1", "m2"], "error": None}


def test_discover_endpoint_models_error_no_throw():
    def boom():
        raise ConnectionError("connection refused")

    out = configs.discover_endpoint_models("config.m5.yaml", lister=boom)
    assert out["models"] == []
    assert "refused" in out["error"]


def test_order_configs_puts_embed_and_vlm_last():
    got = configs.order_configs(
        ["config.embed.yaml", "config.m5.yaml", "config.x.vlm.yaml", "config.a.yaml"]
    )
    assert got == ["config.a.yaml", "config.m5.yaml", "config.embed.yaml", "config.x.vlm.yaml"]


def test_discover_endpoint_models_non_iterable_lister_does_not_raise():
    # a misbehaving lister (returns None) must still degrade to an error, never raise
    out = configs.discover_endpoint_models("config.m5.yaml", lister=lambda: None)
    assert out["models"] == []
    assert out["error"]
