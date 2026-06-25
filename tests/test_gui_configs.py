from __future__ import annotations

from touchstone.gui import configs


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


def test_models_by_config_carries_thinking_fields(tmp_path):
    # the picker template embeds models_by_config as JSON; the thinking knobs must travel with it
    # so the JS can forward them into models_json (GUI eval start).
    cfg = _write(
        tmp_path / "config.thinking.yaml",
        "models:\n  - {id: g, reasoning_headroom_tokens: 4000, extra_body: {a: 1}}\n",
    )
    [m] = configs.models_by_config([cfg])[cfg]
    assert m["reasoning_headroom_tokens"] == 4000
    assert m["extra_body"] == {"a": 1}


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


def test_discover_models_generic_dedupes_and_never_raises():
    from touchstone.gui.configs import discover_models

    out = discover_models("http://x/v1", "k", lister=lambda: ["a", "a", "b"])
    assert out == {"models": ["a", "b"], "error": None}

    boom = discover_models(
        "http://x/v1", "k", lister=lambda: (_ for _ in ()).throw(RuntimeError("dead"))
    )
    assert boom["models"] == [] and "dead" in boom["error"]


def test_discover_judge_endpoint_models(tmp_path):
    from touchstone.gui.configs import discover_judge_endpoint_models

    jc = tmp_path / "judge.yaml"
    jc.write_text("endpoint:\n  base_url: http://x/v1\nmodel: qwen\n", encoding="utf-8")
    out = discover_judge_endpoint_models(str(jc), lister=lambda: ["qwen", "gemma"])
    assert out == {"models": ["qwen", "gemma"], "error": None}


# ── eval_model_options: single-select merge of endpoint-served + config-declared ────────


def test_eval_model_options_endpoint_only_all_served():
    out = configs.eval_model_options(config_models=[], endpoint_models=["a", "b"])
    assert out["default_id"] == "a"  # first served is the default
    assert [(o["id"], o["served"], o["source"]) for o in out["options"]] == [
        ("a", True, "endpoint"),
        ("b", True, "endpoint"),
    ]
    # endpoint-only models get neutral defaults
    a = out["options"][0]
    assert a["quant"] == "" and a["max_tokens_default"] == 400
    assert a["reasoning_headroom_tokens"] == 0 and a["extra_body"] == {}


def test_eval_model_options_endpoint_match_carries_config_knobs():
    cfg = [
        {
            "id": "g",
            "quant": "Q5",
            "max_tokens_default": 300,
            "reasoning_headroom_tokens": 4000,
            "extra_body": {"x": 1},
        }
    ]
    out = configs.eval_model_options(config_models=cfg, endpoint_models=["g"])
    [opt] = out["options"]
    assert opt["served"] is True and opt["source"] == "both"
    # the served model that is ALSO declared keeps the config's thinking knobs (fair compare)
    assert opt["quant"] == "Q5" and opt["max_tokens_default"] == 300
    assert opt["reasoning_headroom_tokens"] == 4000 and opt["extra_body"] == {"x": 1}


def test_eval_model_options_config_only_appended_unserved():
    cfg = [{"id": "declared", "quant": "Q4"}]
    out = configs.eval_model_options(config_models=cfg, endpoint_models=["served"])
    ids = [(o["id"], o["served"], o["source"]) for o in out["options"]]
    # served first, the config-declared-but-not-loaded one appended and flagged
    assert ids == [("served", True, "endpoint"), ("declared", False, "config")]
    assert (
        out["default_id"] == "served"
    )  # default never picks an unserved model when a served one exists


def test_eval_model_options_offline_falls_back_to_config():
    cfg = [{"id": "x", "quant": "Q4"}, {"id": "y"}]
    out = configs.eval_model_options(config_models=cfg, endpoint_models=[])
    assert [(o["id"], o["served"]) for o in out["options"]] == [("x", False), ("y", False)]
    assert out["default_id"] == "x"  # offline → first config model is the default


def test_eval_model_options_both_empty():
    out = configs.eval_model_options(config_models=[], endpoint_models=[])
    assert out == {"options": [], "default_id": None}


def test_eval_model_options_dedupes_config_id_already_served():
    # a config id that is also served must not appear twice
    cfg = [{"id": "dup", "quant": "Q4"}]
    out = configs.eval_model_options(config_models=cfg, endpoint_models=["dup"])
    assert [o["id"] for o in out["options"]] == ["dup"]
    assert out["options"][0]["source"] == "both" and out["options"][0]["quant"] == "Q4"
