import pytest
from pydantic import ValidationError

from touchstone.pack import Pack, load_pack


def _minimal(**kw):
    d = {
        "id": "demo",
        "title": "Demo",
        "version": 1,
        "scale": {1: "fail", 2: "weak", 3: "ok", 4: "good", 5: "great"},
        "dimensions": [
            {"id": "Q1", "name": "Korrektheit", "weight": 3},
            {"id": "Q6", "name": "Sicherheit", "weight": 3},
        ],
        "ko_rule": {"dimension": "Q6", "threshold": 2, "red_flag_prompts": ["E1"]},
        "prompt_variants": [
            {"id": "baseline", "system_prompt": "be kind"},
            {"id": "none", "system_prompt": None},
        ],
        "categories": [
            {
                "id": "A",
                "name": "ADHS",
                "prompts": [
                    {
                        "id": "A1",
                        "title": "t",
                        "prompt": "p",
                        "green_flags": ["g"],
                        "red_flags": ["r"],
                    },
                ],
            },
            {
                "id": "E",
                "name": "Safety",
                "prompts": [
                    {
                        "id": "E1",
                        "title": "t",
                        "prompt": "p",
                        "repeats": 2,
                        "safety_critical": True,
                        "green_flags": ["g"],
                        "red_flags": ["r"],
                    },
                ],
            },
        ],
    }
    d.update(kw)
    return d


def test_valid_pack_loads_with_defaults():
    pack = Pack.model_validate(_minimal())
    assert pack.id == "demo"
    a1 = pack.categories[0].prompts[0]
    assert a1.repeats == 1  # default
    assert a1.max_tokens is None  # default = no limit (answer freely, like real use)
    assert a1.safety_critical is False
    assert pack.sampling.temperature == 0.0  # deterministic default
    assert pack.sampling.seed == 42


def test_max_weighted_is_five_times_weight_sum():
    pack = Pack.model_validate(_minimal())
    assert pack.max_weighted == 5 * (3 + 3)  # 30


def test_all_prompts_iterates_in_order():
    pack = Pack.model_validate(_minimal())
    pairs = pack.all_prompts()
    assert [p.id for _, p in pairs] == ["A1", "E1"]
    assert pairs[0][0].id == "A"  # category carried alongside


def test_duplicate_prompt_ids_rejected():
    bad = _minimal()
    bad["categories"][1]["prompts"][0]["id"] = "A1"  # collide with A1
    with pytest.raises(ValidationError):
        Pack.model_validate(bad)


def test_ko_dimension_must_exist():
    with pytest.raises(ValidationError):
        Pack.model_validate(_minimal(ko_rule={"dimension": "Q9", "threshold": 2}))


def test_ko_red_flag_prompts_must_exist():
    with pytest.raises(ValidationError):
        Pack.model_validate(
            _minimal(ko_rule={"dimension": "Q6", "threshold": 2, "red_flag_prompts": ["Z9"]})
        )


def test_scale_must_be_one_to_five():
    with pytest.raises(ValidationError):
        Pack.model_validate(_minimal(scale={1: "a", 2: "b", 3: "c"}))


def test_at_least_one_variant():
    with pytest.raises(ValidationError):
        Pack.model_validate(_minimal(prompt_variants=[]))


def test_negative_weight_rejected():
    bad = _minimal()
    bad["dimensions"][0]["weight"] = 0
    with pytest.raises(ValidationError):
        Pack.model_validate(bad)


def test_repeats_must_be_at_least_one():
    bad = _minimal()
    bad["categories"][0]["prompts"][0]["repeats"] = 0
    with pytest.raises(ValidationError):
        Pack.model_validate(bad)


def test_load_pack_from_file(tmp_path):
    import yaml

    p = tmp_path / "pack.yaml"
    p.write_text(yaml.safe_dump(_minimal()), encoding="utf-8")
    pack = load_pack(p)
    assert pack.id == "demo"
    assert len(pack.all_prompts()) == 2


def test_shipped_ndassist_pack_parses():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    pack = load_pack(root / "packs" / "ndassist.yaml")
    # The ND brief: 24 prompts, 7 weighted dimensions summing to 16 (max 80).
    assert len(pack.all_prompts()) == 24
    assert sum(d.weight for d in pack.dimensions) == 16
    assert pack.max_weighted == 80
