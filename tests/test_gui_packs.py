from __future__ import annotations

from touchstone.gui import packs

# A minimal-but-valid pack used as the happy-path fixture.
VALID = """
id: demo
title: Demo
scale: {1: a, 2: b, 3: c, 4: d, 5: e}
dimensions:
  - {id: Q1, name: Qualität, weight: 2}
  - {id: Q2, name: Sicherheit, weight: 1}
ko_rule: {dimension: Q2, threshold: 2, red_flag_prompts: [A1]}
prompt_variants:
  - {id: none, system_prompt: null}
categories:
  - id: A
    name: Allgemein
    prompts:
      - {id: A1, title: Erste, prompt: "Frage?"}
      - {id: A2, title: Zweite, prompt: "Noch eine?"}
"""


def test_validate_pack_yaml_ok_with_summary():
    out = packs.validate_pack_yaml(VALID)
    assert out["ok"] is True
    assert out["errors"] == []
    assert out["pack"] is not None
    s = out["summary"]
    assert s["prompts"] == 2
    assert s["dimensions"] == 2
    assert s["variants"] == 1
    assert s["categories"] == 1
    assert s["max_weighted"] == 5 * (2 + 1)  # 5 × Σweights


def test_validate_pack_yaml_parse_error():
    out = packs.validate_pack_yaml("id: [unterminated\n")
    assert out["ok"] is False
    assert out["pack"] is None
    assert out["summary"] is None
    assert out["errors"] and out["errors"][0]["msg"]


def test_validate_pack_yaml_not_a_mapping():
    out = packs.validate_pack_yaml("- just\n- a\n- list\n")
    assert out["ok"] is False
    assert out["errors"]


def test_validate_pack_yaml_empty():
    out = packs.validate_pack_yaml("")
    assert out["ok"] is False
    assert out["errors"]


def test_validate_pack_yaml_scale_not_one_to_five():
    bad = VALID.replace("scale: {1: a, 2: b, 3: c, 4: d, 5: e}", "scale: {1: a, 2: b, 3: c}")
    out = packs.validate_pack_yaml(bad)
    assert out["ok"] is False
    assert any("scale" in e["loc"] or "scale" in e["msg"] for e in out["errors"])


def test_validate_pack_yaml_dangling_ko_dimension():
    bad = VALID.replace("dimension: Q2", "dimension: Q9")
    out = packs.validate_pack_yaml(bad)
    assert out["ok"] is False
    assert any("Q9" in e["msg"] or "ko_rule" in e["loc"] for e in out["errors"])


def test_validate_pack_yaml_duplicate_prompt_id():
    bad = VALID.replace("id: A2, title: Zweite", "id: A1, title: Zweite")
    out = packs.validate_pack_yaml(bad)
    assert out["ok"] is False
    assert any("duplicate" in e["msg"].lower() for e in out["errors"])


def test_validate_pack_yaml_carries_loc_path():
    # a field-level violation must surface a dotted loc so the editor can point at it
    bad = VALID.replace("weight: 2", "weight: 0")  # weight must be positive
    out = packs.validate_pack_yaml(bad)
    assert out["ok"] is False
    assert any("dimensions" in e["loc"] for e in out["errors"])


def test_safe_pack_filename_normalizes_and_confines():
    assert packs.safe_pack_filename("foo") == "foo.yaml"
    assert packs.safe_pack_filename("foo.yaml") == "foo.yaml"
    assert packs.safe_pack_filename("my-pack_2") == "my-pack_2.yaml"


def test_safe_pack_filename_rejects_bad():
    assert packs.safe_pack_filename("") is None
    assert packs.safe_pack_filename("../etc") is None
    assert packs.safe_pack_filename("a/b") is None
    assert packs.safe_pack_filename("sub/pack.yaml") is None
    assert packs.safe_pack_filename("pack.yml") is None  # only .yaml
    assert packs.safe_pack_filename("pack.json") is None
    assert packs.safe_pack_filename("pää ck") is None
    assert packs.safe_pack_filename(".yaml") is None  # empty stem


def test_new_pack_template_is_valid():
    out = packs.validate_pack_yaml(packs.NEW_PACK_TEMPLATE)
    assert out["ok"] is True, out["errors"]
