import pytest
from pydantic import ValidationError

from touchstone.gui.judge_meta import (
    MetaResponse,
    empty_response_template,
    parse_meta_response,
)
from touchstone.pack import load_pack

PACK = "packs/ndassist.yaml"

VALID_YAML = """
cells:
  - model: m
    variant: baseline
    fresh_scores:
      dimensions: {Q1: 4, Q2: 3}
      ko_fired: false
      overall: "Ja"
    critique:
      dimensions:
        Q1: {cites_evidence: true, names_improvement: false, justifies_level: true, catches_safety: true, note: "ok"}
      summary: "solide"
recommendations:
  - "Bei Score < 5 immer benennen, was besser wäre."
"""


def test_parse_valid_response():
    r = parse_meta_response(VALID_YAML)
    assert isinstance(r, MetaResponse)
    assert r.cells[0].model == "m" and r.cells[0].variant == "baseline"
    assert r.cells[0].fresh_scores.dimensions == {"Q1": 4, "Q2": 3}
    assert r.cells[0].critique.dimensions["Q1"].names_improvement is False
    assert r.recommendations == ["Bei Score < 5 immer benennen, was besser wäre."]


def test_parse_rejects_non_mapping():
    with pytest.raises(ValueError):
        parse_meta_response("- just\n- a\n- list\n")


def test_parse_rejects_bad_score_type():
    with pytest.raises(ValidationError):
        parse_meta_response(
            "cells:\n  - model: m\n    variant: v\n"
            "    fresh_scores: {dimensions: {Q1: not_an_int}, ko_fired: false, overall: x}\n"
            "    critique: {dimensions: {}, summary: ''}\n"
        )


def test_empty_template_round_trips():
    pk = load_pack(PACK)
    cells = [("m", "baseline"), ("m", "none")]
    text = empty_response_template(pk, cells)
    r = parse_meta_response(text)
    assert [(c.model, c.variant) for c in r.cells] == cells
    # every pack dimension is present in both fresh_scores and critique for each cell
    dim_ids = {d.id for d in pk.dimensions}
    for c in r.cells:
        assert set(c.fresh_scores.dimensions) == dim_ids
        assert set(c.critique.dimensions) == dim_ids
