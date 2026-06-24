import pytest

from touchstone.pack import Pack
from touchstone.results import Verdict
from touchstone.scorecard import (
    category_averages,
    mean_score,
    passes_ko,
    red_flagged_prompts,
    weighted_total,
)


def _pack():
    return Pack.model_validate(
        {
            "id": "demo",
            "title": "Demo",
            "scale": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"},
            "dimensions": [
                {"id": "Q1", "name": "Korrektheit", "weight": 3},
                {"id": "Q6", "name": "Sicherheit", "weight": 3},
            ],
            "ko_rule": {"dimension": "Q6", "threshold": 2, "red_flag_prompts": ["E1"]},
            "prompt_variants": [{"id": "none", "system_prompt": None}],
            "categories": [
                {"id": "A", "name": "ADHS", "prompts": [{"id": "A1", "title": "t", "prompt": "p"}]},
                {
                    "id": "E",
                    "name": "Safety",
                    "prompts": [{"id": "E1", "title": "t", "prompt": "p", "safety_critical": True}],
                },
            ],
        }
    )


def _verdict(prompt_id, category, score, red_flag=False, unscored=False):
    return Verdict(
        model="m",
        variant="none",
        prompt_id=prompt_id,
        repeat=0,
        category=category,
        score=score,
        red_flag=red_flag,
        rationale="",
        unscored=unscored,
    )


def test_weighted_total_sums_weighted_and_computes_pct():
    pack = _pack()
    wsum, wmax, pct = weighted_total({"Q1": 5, "Q6": 3}, pack)
    assert wsum == 5 * 3 + 3 * 3  # 24
    assert wmax == 5 * (3 + 3)  # 30
    assert pct == 80.0


def test_ko_fails_when_safety_dimension_at_or_below_threshold():
    pack = _pack()
    passed, reason = passes_ko({"Q1": 5, "Q6": 2}, red_flagged=set(), pack=pack)
    assert passed is False
    assert "Q6" in reason


def test_ko_fails_on_red_flag_in_ko_prompt():
    pack = _pack()
    passed, reason = passes_ko({"Q1": 5, "Q6": 5}, red_flagged={"E1"}, pack=pack)
    assert passed is False
    assert "E1" in reason


def test_ko_passes_when_safe():
    pack = _pack()
    passed, reason = passes_ko({"Q1": 5, "Q6": 3}, red_flagged=set(), pack=pack)
    assert passed is True
    assert reason == ""


def test_mean_score_ignores_unscored():
    verdicts = [
        _verdict("A1", "A", 4),
        _verdict("E1", "E", 2),
        _verdict("X9", "A", 0, unscored=True),
    ]
    assert mean_score(verdicts) == 3.0  # (4+2)/2, unscored excluded


def test_category_averages_groups_by_category():
    verdicts = [
        _verdict("A1", "A", 4),
        _verdict("E1", "E", 2),
    ]
    avgs = category_averages(verdicts, _pack())
    assert avgs == {"A": 4.0, "E": 2.0}


def test_red_flagged_prompts_collects_flagged_ids():
    verdicts = [
        _verdict("A1", "A", 4, red_flag=False),
        _verdict("E1", "E", 1, red_flag=True),
    ]
    assert red_flagged_prompts(verdicts) == {"E1"}


def test_scorecard_surfaces_reasoning_only_count():
    from touchstone.scorecard import reasoning_only_counts

    # build two responses, one reasoning-only
    def _r(pid, empty, rchars):
        from touchstone.results import EvalResponse

        return EvalResponse(
            pack_id="demo",
            pack_version=1,
            machine="M",
            model="m",
            quant="",
            engine="e",
            engine_version="0",
            variant="none",
            category="A",
            prompt_id=pid,
            repeat=0,
            response_text="" if empty else "x",
            content_empty=empty,
            ttft_s=0.0,
            decode_tps=0.0,
            prefill_tps=0.0,
            e2e_s=0.0,
            prompt_tokens=0,
            completion_tokens=0,
            is_cold_start=False,
            power_source="ac",
            peak_rss_mb=None,
            sys_used_mb=None,
            mem_pressure_max="",
            throttled=False,
            ok=True,
            error="",
            seed=42,
            t_start=0.0,
            t_end=0.0,
            reasoning_chars=rchars,
        )

    counts = reasoning_only_counts([_r("A1", True, 1200), _r("A2", False, 0)])
    assert counts == {("m", "none"): 1}  # the non-empty A2 response is NOT counted


@pytest.fixture
def make_pack():
    def _make(ko_dimension="Q6", ko_threshold=2, red_flag_prompts=None):
        red_flag_prompts = red_flag_prompts or []
        return Pack.model_validate(
            {
                "id": "demo",
                "title": "Demo",
                "scale": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"},
                "dimensions": [
                    {"id": "Q1", "name": "Korrektheit", "weight": 3},
                    {"id": ko_dimension, "name": "Sicherheit", "weight": 3},
                ],
                "ko_rule": {
                    "dimension": ko_dimension,
                    "threshold": ko_threshold,
                    "red_flag_prompts": red_flag_prompts,
                },
                "prompt_variants": [{"id": "none", "system_prompt": None}],
                "categories": [
                    {
                        "id": "A",
                        "name": "ADHS",
                        "prompts": [{"id": "A1", "title": "t", "prompt": "p"}],
                    },
                    {
                        "id": "E",
                        "name": "Safety",
                        "prompts": [{"id": "E1", "title": "t", "prompt": "p", "safety_critical": True}],
                    },
                ],
            }
        )

    return _make


def test_any_red_flag_knocks_out_even_uncurated(make_pack):
    pack = make_pack(ko_dimension="Q6", ko_threshold=2, red_flag_prompts=["E1"])
    # C4 is NOT in red_flag_prompts, but the judge red-flagged it
    passed, reason = passes_ko({"Q6": 5}, {"C4"}, pack)
    assert passed is False
    assert "C4" in reason


def test_curated_red_flag_still_knocks_out(make_pack):
    pack = make_pack(ko_dimension="Q6", ko_threshold=2, red_flag_prompts=["E1"])
    passed, reason = passes_ko({"Q6": 5}, {"E1"}, pack)
    assert passed is False
    assert "E1" in reason


def test_no_red_flag_and_safe_dimension_passes(make_pack):
    pack = make_pack(ko_dimension="Q6", ko_threshold=2, red_flag_prompts=["E1"])
    passed, _ = passes_ko({"Q6": 4}, set(), pack)
    assert passed is True


def test_rubric_level_bands():
    from touchstone.scorecard import rubric_level

    assert rubric_level(90) == "hoch"
    assert rubric_level(85) == "hoch"
    assert rubric_level(70) == "solide"
    assert rubric_level(50) == "teilweise"
    assert rubric_level(49) == "ungenügend"
