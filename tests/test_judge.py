import pytest

from touchstone.judge import (
    judge_bundle,
    judge_responses,
    parse_dimension_report,
    parse_dimension_scores,
    parse_verdict,
    score_dimensions,
    score_response,
)
from touchstone.pack import Pack


def _make_pack() -> Pack:
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
                        {
                            "id": "A1",
                            "title": "t",
                            "prompt": "p",
                            "green_flags": ["g"],
                            "red_flags": ["r"],
                        }
                    ],
                },
                {
                    "id": "E",
                    "name": "Safety",
                    "prompts": [{"id": "E1", "title": "t", "prompt": "p", "safety_critical": True}],
                },
            ],
        }
    )


@pytest.fixture()
def _pack() -> Pack:
    return _make_pack()


def _resp(prompt_id, category, text="some answer", content_empty=False):
    from touchstone.results import EvalResponse

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
    raw = 'Sure!\n```json\n{"score": 5, "red_flag": true, "rationale": "x"}\n```\nDone.'
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
    pack = _make_pack()
    dims = parse_dimension_scores(
        '{"Q1": {"score": 4, "rationale": "x"}, "Q6": {"score": 9, "rationale": "E1 schwach"}}',
        pack,
    )
    assert dims == {"Q1": 4, "Q6": 5}  # Q6 clamped to 5


def test_score_response_calls_backend_and_builds_verdict():
    pack = _make_pack()
    backend = FakeBackend('{"score": 4, "red_flag": false, "rationale": "ok"}')
    _, prompt = pack.all_prompts()[0]  # A1
    v = score_response(backend, _resp("A1", "A"), prompt, pack)
    assert v.score == 4
    assert v.prompt_id == "A1"
    assert len(backend.calls) == 1


def test_score_prompt_persona_is_pack_neutral():
    # The per-prompt judge persona must not hardcode one pack's domain: it framed
    # EVERY pack as a "Bewerter ... für neurodivergente Menschen", which biases
    # scoring for non-ND packs (e.g. buero, Büro-/Wissensarbeit) toward ND-warmth
    # instead of terse factual fidelity. The domain context already lives in the
    # user prompt (tests + green/red flags + source text).
    pack = _make_pack()
    backend = FakeBackend('{"score": 3, "red_flag": false, "rationale": "x"}')
    _, prompt = pack.all_prompts()[0]  # A1, non-empty answer → backend is called
    score_response(backend, _resp("A1", "A"), prompt, pack)
    system, _user = backend.calls[0]
    assert "neurodivergent" not in system.lower()
    assert "Green/Red-Flags" in system  # still anchored to the rubric


def test_score_response_empty_content_autoscored_without_backend():
    pack = _make_pack()
    backend = FakeBackend('{"score": 5, "red_flag": false, "rationale": "x"}')
    # E1 is safety_critical → empty content is a red flag, scored 1, no backend call
    e1 = pack.categories[1].prompts[0]
    v = score_response(backend, _resp("E1", "E", text="", content_empty=True), e1, pack)
    assert v.score == 1
    assert v.red_flag is True
    assert v.unscored is False
    assert backend.calls == []


def test_judge_responses_one_verdict_per_response():
    pack = _make_pack()
    backend = FakeBackend('{"score": 3, "red_flag": false, "rationale": "ok"}')
    responses = [_resp("A1", "A"), _resp("E1", "E")]
    verdicts = judge_responses(backend, responses, pack)
    assert len(verdicts) == 2
    assert {v.prompt_id for v in verdicts} == {"A1", "E1"}


def test_score_dimensions_parses_master_report():
    pack = _make_pack()
    backend = FakeBackend(
        '{"Q1": {"score": 4, "rationale": "x"}, "Q6": {"score": 3, "rationale": "E1 schwach"}}'
    )
    report = score_dimensions(backend, pack, model="m", variant="none", verdicts=[])
    assert report.dim_scores == {"Q1": 4, "Q6": 3}
    assert report.model == "m"
    assert report.dim_rationales["Q6"]


def test_judge_responses_skips_skip_keys_and_calls_on_verdict():
    pack = _make_pack()
    backend = FakeBackend('{"score": 3, "red_flag": false, "rationale": "ok"}')
    responses = [_resp("A1", "A"), _resp("E1", "E")]
    recorded = []
    skip = {("m", "none", "A1", 0)}  # A1 already judged in a prior (interrupted) run
    verdicts = judge_responses(backend, responses, pack, skip_keys=skip, on_verdict=recorded.append)
    assert [v.prompt_id for v in verdicts] == ["E1"]  # A1 skipped
    assert len(recorded) == 1  # on_verdict fired for the freshly judged one


def test_judge_bundle_resume_merges_prior_and_judges_only_new():
    from touchstone.results import Verdict

    pack = _make_pack()
    backend = FakeBackend('{"score": 3, "red_flag": false, "rationale": "ok"}')
    responses = [_resp("A1", "A"), _resp("E1", "E")]
    prior = [Verdict("m", "none", "A1", 0, "A", 4, False, "prior")]
    recorded = []
    verdicts, reports = judge_bundle(
        backend, responses, pack, prior_verdicts=prior, on_verdict=recorded.append
    )
    assert {v.prompt_id for v in verdicts} == {"A1", "E1"}  # prior + new
    assert len(recorded) == 1  # only E1 newly judged
    assert reports  # master reports still computed


def test_load_judgements_jsonl_tolerates_bad_last_line(tmp_path):
    import json

    from touchstone.judge import load_judgements_jsonl
    from touchstone.results import Verdict

    p = tmp_path / "judgements.jsonl"
    v = Verdict("m", "none", "A1", 0, "A", 4, False, "ok")
    p.write_text(json.dumps(v.as_dict()) + "\n" + '{"bad": ', encoding="utf-8")
    out = load_judgements_jsonl(p)
    assert len(out) == 1
    assert out[0].prompt_id == "A1"


def test_parse_dimension_report_nested_scores_and_rationales(_pack):
    raw = '{"Q1": {"score": 4, "rationale": "solide bei A1, A2"}, "Q6": {"score": 2, "rationale": "unsicher bei E1"}}'
    scores, rationales = parse_dimension_report(raw, _pack)
    assert scores == {"Q1": 4, "Q6": 2}
    assert "E1" in rationales["Q6"]


def test_parse_dimension_report_tolerates_bare_int(_pack):
    # judge drift: a bare int at the dim key still parses, rationale defaults to ""
    scores, rationales = parse_dimension_report('{"Q1": 4}', _pack)
    assert scores == {"Q1": 4}
    assert rationales["Q1"] == ""


def test_parse_dimension_report_trailing_prose_with_brace(_pack):
    """MINOR 8: valid JSON followed by prose containing a brace token still parses.

    A greedy rfind('}') over-captures here and drops the whole report; raw_decode of the
    first complete object survives.
    """
    raw = '{"Q1": {"score": 4, "rationale": "ok bei A1"}} Anmerkung: {note} folgt.'
    scores, rationales = parse_dimension_report(raw, _pack)
    assert scores == {"Q1": 4}
    assert "A1" in rationales["Q1"]


def test_parse_dimension_report_keeps_rationale_when_score_unparseable(_pack):
    """NIT 10: an unparseable score must not also drop the rationale (the only explanation)."""
    raw = '{"Q1": {"score": "n/a", "rationale": "schwach bei A1"}}'
    scores, rationales = parse_dimension_report(raw, _pack)
    assert "Q1" not in scores  # score guard still drops the unparseable number
    assert rationales["Q1"] == "schwach bei A1"  # but the rationale survives


def test_score_response_reasoning_only_is_unscored(_pack):
    from touchstone.judge import score_response

    prompt = _pack.all_prompts()[0][1]

    class _NoBackend:
        def judge(self, *, system, user):
            raise AssertionError("judge must not be called for empty content")

    r = _resp("A1", "A", text="", content_empty=True)
    r.reasoning_chars = 1423  # reasoning-only
    v = score_response(_NoBackend(), r, prompt, _pack)
    assert v.unscored is True
    assert v.red_flag is False
    assert v.score == 0  # unscored → score is the inert 0, never a false 1/5
    assert "reasoning" in v.rationale.lower()


def test_score_response_truly_empty_still_scores_one(_pack):
    from touchstone.judge import score_response

    prompt = _pack.all_prompts()[0][1]

    class _NoBackend:
        def judge(self, *, system, user):
            raise AssertionError("no judge call")

    r = _resp("A1", "A", text="", content_empty=True)
    r.reasoning_chars = 0  # genuinely empty, no thinking
    v = score_response(_NoBackend(), r, prompt, _pack)
    assert v.unscored is False
    assert v.score == 1


def test_dimension_prompt_has_rationale_quality_rules(_pack):
    from touchstone.judge import _build_dimension_prompt

    system, _user = _build_dimension_prompt(_pack, [])
    assert "Beobachtung" in system  # D3: concrete observation, not a bare pointer
    assert "nicht eins höher" in system and "nicht eins tiefer" in system  # D2: level justification
    assert "Wert < 5" in system  # D4: name the concrete fix
    assert "Dimensions-Lokus" in system  # D5: locus discipline
    assert "Red-Flag" in system and "K.-o.-Dimension" in system  # D1: safety reconciliation
    # D1 scoped: a format/tone/style red-flag must NOT force the safety K.-o. dimension
    assert "NUR Format" in system
    assert "3-4 Sätze" in system and "<1 Satz>" not in system  # richer rationale


def test_dimension_prompt_marks_ko_dimension(_pack):
    from touchstone.judge import _build_dimension_prompt

    _system, user = _build_dimension_prompt(_pack, [])
    # _make_pack ko_rule: dimension Q6, threshold 2
    q6 = [ln for ln in user.splitlines() if ln.strip().startswith("Q6 =")]
    q1 = [ln for ln in user.splitlines() if ln.strip().startswith("Q1 =")]
    # pin the disqualification OPERATOR (≤), not just the floor value — engine: score <= threshold
    assert q6 and "K.-o.-Dimension" in q6[0] and "Boden 2" in q6[0] and "≤ 2" in q6[0]
    assert q1 and "K.-o.-Dimension" not in q1[0]  # non-KO dimension is not marked


def test_dimension_prompt_evidence_carries_category_and_rationale():
    from touchstone.judge import _build_dimension_prompt
    from touchstone.results import Verdict

    pack = _make_pack()
    v = Verdict(
        model="m",
        variant="none",
        prompt_id="E1",
        repeat=0,
        category="E",
        score=2,
        red_flag=True,
        rationale="erfand eine Quelle",
    )
    _system, user = _build_dimension_prompt(pack, [v])
    # the holistic judge must SEE the per-answer note + category to ground rules 1/4/5
    assert "[E] E1: score 2 · RED FLAG — erfand eine Quelle" in user


def test_dimension_prompt_sanitizes_multiline_rationale():
    from touchstone.judge import _build_dimension_prompt
    from touchstone.results import Verdict

    pack = _make_pack()
    v = Verdict(
        model="m",
        variant="none",
        prompt_id="E1",
        repeat=0,
        category="E",
        score=2,
        red_flag=True,
        rationale="erste Zeile\n- punkt\n\nzweite   Zeile",
    )
    _system, user = _build_dimension_prompt(pack, [v])
    # raw judge text may contain newlines/markdown → must collapse to exactly one evidence line
    ev = [ln for ln in user.splitlines() if "E1: score 2" in ln]
    assert len(ev) == 1
    assert "erste Zeile - punkt zweite Zeile" in ev[0]


def _fake_create_capturing(captured):
    from types import SimpleNamespace

    def _create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"score": 3}'))]
        )

    return _create


def test_judge_backend_suppresses_thinking_by_default():
    from touchstone.judge import SUPPRESS_THINKING_BODY, OpenAIJudgeBackend

    b = OpenAIJudgeBackend("http://localhost:1234/v1", "k", "m")
    captured: dict = {}
    b._client.chat.completions.create = _fake_create_capturing(captured)  # type: ignore[method-assign]
    b.judge(system="s", user="u")
    eb = captured.get("extra_body") or {}
    assert eb.get("reasoning_effort") == "none"
    assert eb.get("chat_template_kwargs") == {"enable_thinking": False}
    assert eb.get("reasoning_budget") == 0
    assert eb == SUPPRESS_THINKING_BODY


def test_judge_backend_keeps_thinking_when_disabled():
    from touchstone.judge import OpenAIJudgeBackend

    b = OpenAIJudgeBackend("http://localhost:1234/v1", "k", "m", suppress_thinking=False)
    captured: dict = {}
    b._client.chat.completions.create = _fake_create_capturing(captured)  # type: ignore[method-assign]
    b.judge(system="s", user="u")
    assert "extra_body" not in captured  # cloud judge that rejects the hints stays clean


def test_judge_config_suppresses_thinking_by_default():
    from touchstone.judge import JudgeConfig

    jc = JudgeConfig.model_validate(
        {"endpoint": {"base_url": "http://localhost:1234/v1"}, "model": "m"}
    )
    assert jc.suppress_thinking is True
