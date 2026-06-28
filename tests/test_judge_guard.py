from types import SimpleNamespace

import pytest

from touchstone import judge as J
from touchstone.pack import Pack
from touchstone.results import EvalResponse, Verdict


def _make_pack() -> Pack:
    return Pack.model_validate(
        {
            "id": "demo",
            "title": "Demo",
            "scale": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"},
            "dimensions": [{"id": "Q1", "name": "Korrektheit", "weight": 3}],
            "ko_rule": {"dimension": "Q1", "threshold": 2},
            "prompt_variants": [{"id": "none", "system_prompt": None}],
            "categories": [
                {
                    "id": "A",
                    "name": "ADHS",
                    "prompts": [{"id": "A1", "title": "t", "prompt": "p", "green_flags": ["g"]}],
                }
            ],
        }
    )


def _mk_resp(prompt_id="A1", category="A", repeat=0, *, content_empty=False, reasoning_chars=0):
    return EvalResponse(
        pack_id="demo",
        pack_version=1,
        machine="M",
        model="m",
        quant="n/a",
        engine="x",
        engine_version="0",
        variant="none",
        category=category,
        prompt_id=prompt_id,
        repeat=repeat,
        response_text="eine Antwort",
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
        reasoning_chars=reasoning_chars,
    )


class _ThrowBackend:
    """Judge backend that raises JudgeCallError for the first ``fail_first`` calls, then succeeds."""

    def __init__(self, fail_first: int = 999) -> None:
        self.calls = 0
        self.fail_first = fail_first

    def judge(self, *, system: str, user: str) -> str:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise J.JudgeCallError("boom")
        return '{"score": 4, "red_flag": false, "rationale": "ok"}'


# --------------------------------------------------------------- Task 1: Verdict
def test_verdict_judge_error_defaults_false_and_roundtrips():
    v = Verdict(
        model="m",
        variant="none",
        prompt_id="A1",
        repeat=0,
        category="A",
        score=0,
        red_flag=False,
        rationale="x",
    )
    assert v.judge_error is False
    d = v.as_dict()
    assert d["judge_error"] is False
    assert Verdict(**d).judge_error is False
    assert (
        Verdict(
            model="m",
            variant="none",
            prompt_id="A1",
            repeat=0,
            category="A",
            score=0,
            red_flag=False,
            rationale="x",
            judge_error=True,
        ).judge_error
        is True
    )


# ---------------------------------------------------- Task 2: bounded backend
class _FakeCompletions:
    def __init__(self, exc=None, content="ok"):
        self.exc, self.content, self.kwargs = exc, content, None

    def create(self, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def _backend_with(fake):
    b = J.OpenAIJudgeBackend.__new__(J.OpenAIJudgeBackend)
    b._model, b._temperature, b._max_tokens = "jm", 0.0, None
    b._suppress_thinking = False  # guard mechanics only; suppression is tested in test_judge.py
    b._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return b


def test_judge_wraps_openai_error_as_judgecallerror():
    from openai import OpenAIError

    b = _backend_with(_FakeCompletions(exc=OpenAIError("boom")))
    with pytest.raises(J.JudgeCallError):
        b.judge(system="s", user="u")


def test_judge_does_not_mask_non_openai_errors():
    # a programmer/client bug must surface as itself, not be swallowed as a judge failure
    b = _backend_with(_FakeCompletions(exc=RuntimeError("bug")))
    with pytest.raises(RuntimeError):
        b.judge(system="s", user="u")


def test_judge_returns_content_on_success():
    assert _backend_with(_FakeCompletions(content="hi")).judge(system="s", user="u") == "hi"


def test_judge_forwards_max_tokens_only_when_set():
    fake = _FakeCompletions(content="x")
    b = _backend_with(fake)
    b._max_tokens = 256
    b.judge(system="s", user="u")
    assert fake.kwargs["max_tokens"] == 256
    fake2 = _FakeCompletions(content="x")
    _backend_with(fake2).judge(system="s", user="u")
    assert "max_tokens" not in fake2.kwargs


# ------------------------------------------------------- Task 3: JudgeConfig
def test_judge_config_guard_defaults():
    jc = J.JudgeConfig(endpoint=J.JudgeEndpoint(base_url="x"), model="m")
    assert jc.call_timeout_s == 120
    assert jc.max_consecutive_failures == 3
    assert jc.max_tokens is None


# ----------------------------------- Task 4: judge_responses degrade + breaker
def test_judge_responses_marks_call_failure_as_judge_error():
    pack = _make_pack()
    vs = J.judge_responses(_ThrowBackend(), [_mk_resp()], pack)  # threshold 0 = no abort
    assert vs[0].judge_error is True and vs[0].unscored is True
    assert "Judge-Fehler" in vs[0].rationale


def test_judge_responses_circuit_breaker_aborts():
    pack = _make_pack()
    resps = [_mk_resp(repeat=i) for i in range(5)]
    with pytest.raises(J.JudgeAborted):
        J.judge_responses(_ThrowBackend(), resps, pack, max_consecutive_failures=3)


def test_judge_responses_resets_counter_on_success():
    pack = _make_pack()
    resps = [_mk_resp(repeat=i) for i in range(5)]
    vs = J.judge_responses(_ThrowBackend(fail_first=2), resps, pack, max_consecutive_failures=3)
    assert sum(1 for v in vs if v.judge_error) == 2
    assert sum(1 for v in vs if not v.judge_error) == 3


def test_judge_responses_does_not_persist_error_verdicts():
    pack = _make_pack()
    persisted: list = []
    J.judge_responses(_ThrowBackend(), [_mk_resp()], pack, on_verdict=persisted.append)
    assert persisted == []  # error verdict NOT streamed → resume re-judges the cell


# ---------------------------------------------- Task 5: score_dimensions degrade
def test_score_dimensions_degrades_on_call_error():
    pack = _make_pack()

    class _Throw:
        def judge(self, *, system, user):
            raise J.JudgeCallError("boom")

    rep = J.score_dimensions(_Throw(), pack, model="m", variant="none", verdicts=[])
    assert rep.dim_scores == {}
    assert "_error" in rep.dim_rationales


# ------------------------------------------- Task 6: judge_bundle threads threshold
def test_judge_bundle_threads_threshold():
    pack = _make_pack()
    resps = [_mk_resp(repeat=i) for i in range(5)]
    with pytest.raises(J.JudgeAborted):
        J.judge_bundle(_ThrowBackend(), resps, pack, max_consecutive_failures=3)


# -------------------------------------------------- Task 8: shipped example docs
def test_judge_example_documents_guard_fields():
    import yaml

    with open("judge.example.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    assert "call_timeout_s" in raw
    assert "max_consecutive_failures" in raw
    assert "max_tokens" in raw


# ----------------------------- review fixes: persist-filter + CLI abort (pinned)
def test_judge_and_persist_filters_error_cells_from_disk(tmp_path):
    # the load-bearing invariant: a judge_error cell never lands in judgements.jsonl (so a resume
    # with a good model re-judges it). Exercises the clean-rewrite filter, not just on_verdict.
    from touchstone import cli
    from touchstone.judge import load_judgements_jsonl

    jpath = tmp_path / "judgements.jsonl"
    cli._judge_and_persist(
        _ThrowBackend(), [_mk_resp()], _make_pack(), [], jpath, max_consecutive_failures=0
    )
    assert jpath.read_text(encoding="utf-8").strip() == ""  # nothing persisted
    assert load_judgements_jsonl(jpath) == []  # resume sees no done cell → re-judges it


def test_judge_cli_aborts_with_exit_1_on_runaway(tmp_path, monkeypatch):
    # the CLI must catch JudgeAborted → clean Exit(1) + message (no traceback, no zombie card).
    import types

    import typer.testing

    from touchstone.cli import app

    class _AlwaysThrow:
        def judge(self, *, system, user):
            raise J.JudgeCallError("timeout")

    b = tmp_path / "bundle"
    b.mkdir()
    (b / "bundle.json").write_text('{"pack_path":"x","host":{}}', encoding="utf-8")
    monkeypatch.setattr("touchstone.cli.load_pack", lambda p: _make_pack())
    monkeypatch.setattr(
        "touchstone.cli.load_responses_jsonl", lambda p: [_mk_resp(repeat=i) for i in range(3)]
    )
    monkeypatch.setattr("touchstone.cli.OpenAIJudgeBackend", lambda *a, **k: _AlwaysThrow())
    monkeypatch.setattr(
        "touchstone.cli.load_judge_config",
        lambda p: types.SimpleNamespace(
            endpoint=types.SimpleNamespace(base_url="x", api_key="y"),
            model="m",
            temperature=0.0,
            call_timeout_s=120,
            max_consecutive_failures=3,
            max_tokens=None,
            suppress_thinking=False,
        ),
    )
    res = typer.testing.CliRunner().invoke(
        app, ["judge", "--bundle", str(b), "--judge-config", "judge.yaml"]
    )
    assert res.exit_code == 1, res.output
    assert "abgebrochen" in res.output
    jpath = b / "judgements.jsonl"  # no error cell persisted on the abort path either
    assert (not jpath.exists()) or jpath.read_text(encoding="utf-8").strip() == ""
