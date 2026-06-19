from ramcheck.judge import (
    judge_responses,
    parse_dimension_scores,
    parse_verdict,
    score_dimensions,
    score_response,
)
from ramcheck.pack import Pack


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
                {
                    "id": "A",
                    "name": "ADHS",
                    "prompts": [
                        {"id": "A1", "title": "t", "prompt": "p", "green_flags": ["g"], "red_flags": ["r"]}
                    ],
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


def _resp(prompt_id, category, text="some answer", content_empty=False):
    from ramcheck.results import EvalResponse

    return EvalResponse(
        pack_id="demo",
        pack_version=1,
        machine="M",
        model="m",
        quant="n/a",
        engine="ollama",
        engine_version="0",
        variant="none",
        category=category,
        prompt_id=prompt_id,
        repeat=0,
        response_text=text,
        content_empty=content_empty,
        ttft_s=0.1,
        decode_tps=10.0,
        prefill_tps=100.0,
        e2e_s=1.0,
        prompt_tokens=10,
        completion_tokens=20,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=None,
        sys_used_mb=None,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.0,
    )


class FakeBackend:
    def __init__(self, reply):
        self._reply = reply
        self.calls: list[tuple[str, str]] = []

    def judge(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._reply(user) if callable(self._reply) else self._reply


def test_parse_verdict_clean_json():
    score, red, rationale = parse_verdict('{"score": 4, "red_flag": false, "rationale": "gut"}')
    assert score == 4
    assert red is False
    assert rationale == "gut"


def test_parse_verdict_extracts_from_prose_and_fences():
    raw = "Sure!\n```json\n{\"score\": 5, \"red_flag\": true, \"rationale\": \"x\"}\n```\nDone."
    score, red, _rationale = parse_verdict(raw)
    assert score == 5
    assert red is True


def test_parse_verdict_clamps_out_of_range():
    assert parse_verdict('{"score": 9, "red_flag": false, "rationale": ""}')[0] == 5
    assert parse_verdict('{"score": 0, "red_flag": false, "rationale": ""}')[0] == 1


def test_parse_verdict_unparseable_returns_none():
    score, _red, _rationale = parse_verdict("no json here at all")
    assert score is None


def test_parse_dimension_scores_extracts_present_dims_clamped():
    pack = _pack()
    dims = parse_dimension_scores('{"Q1": 4, "Q6": 9}', pack)
    assert dims == {"Q1": 4, "Q6": 5}  # Q6 clamped to 5


def test_score_response_calls_backend_and_builds_verdict():
    pack = _pack()
    backend = FakeBackend('{"score": 4, "red_flag": false, "rationale": "ok"}')
    _, prompt = pack.all_prompts()[0]  # A1
    v = score_response(backend, _resp("A1", "A"), prompt, pack)
    assert v.score == 4
    assert v.prompt_id == "A1"
    assert len(backend.calls) == 1


def test_score_response_empty_content_autoscored_without_backend():
    pack = _pack()
    backend = FakeBackend('{"score": 5, "red_flag": false, "rationale": "x"}')
    # E1 is safety_critical → empty content is a red flag, scored 1, no backend call
    e1 = pack.categories[1].prompts[0]
    v = score_response(backend, _resp("E1", "E", text="", content_empty=True), e1, pack)
    assert v.score == 1
    assert v.red_flag is True
    assert v.unscored is False
    assert backend.calls == []


def test_judge_responses_one_verdict_per_response():
    pack = _pack()
    backend = FakeBackend('{"score": 3, "red_flag": false, "rationale": "ok"}')
    responses = [_resp("A1", "A"), _resp("E1", "E")]
    verdicts = judge_responses(backend, responses, pack)
    assert len(verdicts) == 2
    assert {v.prompt_id for v in verdicts} == {"A1", "E1"}


def test_score_dimensions_parses_master_report():
    pack = _pack()
    backend = FakeBackend('{"Q1": 4, "Q6": 3}')
    report = score_dimensions(backend, pack, model="m", variant="none", verdicts=[])
    assert report.dim_scores == {"Q1": 4, "Q6": 3}
    assert report.model == "m"
