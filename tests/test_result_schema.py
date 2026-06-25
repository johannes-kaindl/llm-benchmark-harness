"""Tests for touchstone.result_schema — canonical result.json schema + builder."""

import pytest

from touchstone.pack import Pack
from touchstone.result_schema import ResultDoc, build_result_doc
from touchstone.results import EvalResponse, ModelReport, Verdict

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _resp(
    prompt_id: str,
    *,
    model: str = "m",
    variant: str = "baseline",
    e2e_s: float = 1.0,
    completion_tokens: int = 20,
    is_cold_start: bool = False,
) -> EvalResponse:
    return EvalResponse(
        pack_id="demo",
        pack_version=1,
        machine="M",
        model=model,
        quant="q4",
        engine="ollama",
        engine_version="0",
        variant=variant,
        category="A",
        prompt_id=prompt_id,
        repeat=0,
        response_text="x",
        content_empty=False,
        ttft_s=0.2,
        decode_tps=12.0,
        prefill_tps=100.0,
        e2e_s=e2e_s,
        prompt_tokens=10,
        completion_tokens=completion_tokens,
        is_cold_start=is_cold_start,
        power_source="ac",
        peak_rss_mb=None,
        sys_used_mb=None,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=e2e_s,
    )


def _report(model: str, variant: str, dim_scores: dict[str, int]) -> ModelReport:
    return ModelReport(model=model, variant=variant, dim_scores=dim_scores)


def _verdict(
    model: str,
    variant: str,
    prompt_id: str,
    *,
    score: int = 4,
    red_flag: bool = False,
) -> Verdict:
    return Verdict(
        model=model,
        variant=variant,
        prompt_id=prompt_id,
        repeat=0,
        category="A",
        score=score,
        red_flag=red_flag,
        rationale="ok",
    )


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
                        "prompts": [
                            {"id": "E1", "title": "t", "prompt": "p", "safety_critical": True}
                        ],
                    },
                ],
            }
        )

    return _make


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_perf_only_doc_has_null_quality(make_pack):
    pack = make_pack()
    resps = [_resp("A1", model="m", variant="baseline", e2e_s=1.0, completion_tokens=100)]
    doc = build_result_doc(
        pack,
        resps,
        [],
        [],
        host={"chip": "Apple M5 Pro", "ram_gb": "64 GB"},
        build_meta=None,
        judge=None,
        sampling={"seed": 42, "temperature": 0.0},
    )
    assert doc.schema_version == 1
    assert doc.provenance.chip == "Apple M5 Pro"
    assert doc.cells[0].quality is None
    assert doc.cells[0].answer_tokens_med == 100


def test_full_doc_carries_quality_and_red_flags(make_pack):
    pack = make_pack()
    resps = [_resp("C4", model="m", variant="baseline", e2e_s=1.0, completion_tokens=100)]
    reports = [_report("m", "baseline", {d.id: 4 for d in pack.dimensions})]
    verdicts = [_verdict("m", "baseline", "C4", score=5, red_flag=True)]
    doc = build_result_doc(
        pack,
        resps,
        verdicts,
        reports,
        host={"chip": "x", "ram_gb": "64 GB"},
        build_meta=None,
        judge={"model": "qwen", "temperature": 0.0},
        sampling={"seed": 42, "temperature": 0.0},
    )
    cell = doc.cells[0]
    assert cell.quality.safety_passed is False  # C4 red flag
    assert "C4" in cell.quality.red_flags
    assert cell.quality.rubric_level in {"hoch", "solide", "teilweise", "ungenügend"}
    assert doc.judge.model == "qwen"


def test_round_trips_through_json(make_pack):
    doc = build_result_doc(
        make_pack(),
        [_resp("A1", model="m", variant="baseline", e2e_s=1.0, completion_tokens=10)],
        [],
        [],
        host={"chip": "x", "ram_gb": "1 GB"},
        build_meta=None,
        judge=None,
        sampling={"seed": 1, "temperature": 0.0},
    )
    again = ResultDoc.model_validate_json(doc.model_dump_json())
    assert again == doc
