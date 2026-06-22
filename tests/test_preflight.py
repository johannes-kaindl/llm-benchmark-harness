from ramcheck.config import ModelSpec
from ramcheck.preflight import preflight_models
from ramcheck.runner import StreamEvent


class _FakeClient:
    engine = "fake"
    engine_version = "0"

    def __init__(self, mode):
        self.mode = mode  # "content" | "reasoning" | "empty" | "boom"

    def stream(self, *, messages, model, max_tokens, temperature, seed, extra_body=None):
        if self.mode == "boom":
            raise RuntimeError("model not found")
        if self.mode == "content":
            yield StreamEvent(delta_text="4")
        if self.mode == "reasoning":
            yield StreamEvent(reasoning_text="denke…")
        yield StreamEvent(prompt_tokens=3, completion_tokens=1)


class _MultiModeClient:
    """Yields a different mode per stream() call, in order — to test that preflight_models
    processes every model independently (no short-circuit after the first)."""

    engine = "fake"
    engine_version = "0"

    def __init__(self, modes):
        self._modes = list(modes)
        self._i = 0

    def stream(self, *, messages, model, max_tokens, temperature, seed, extra_body=None):
        mode = self._modes[self._i]
        self._i += 1
        if mode == "content":
            yield StreamEvent(delta_text="4")
        if mode == "reasoning":
            yield StreamEvent(reasoning_text="denke…")
        yield StreamEvent(prompt_tokens=3, completion_tokens=1)


def _budget(_m):
    return 64


def test_preflight_classifies_content():
    [r] = preflight_models(_FakeClient("content"), [ModelSpec(id="m")], budget_for=_budget)
    assert (r.status, r.model) == ("ok", "m")


def test_preflight_classifies_reasoning_only():
    [r] = preflight_models(_FakeClient("reasoning"), [ModelSpec(id="m")], budget_for=_budget)
    assert r.status == "reasoning_only"
    assert r.reasoning_chars > 0


def test_preflight_classifies_empty():
    [r] = preflight_models(_FakeClient("empty"), [ModelSpec(id="m")], budget_for=_budget)
    assert r.status == "empty"


def test_preflight_never_raises_on_error():
    [r] = preflight_models(_FakeClient("boom"), [ModelSpec(id="m")], budget_for=_budget)
    assert r.status == "error"
    assert "not found" in r.detail


def test_preflight_processes_all_models_independently():
    results = preflight_models(
        _MultiModeClient(["content", "reasoning"]),
        [ModelSpec(id="a"), ModelSpec(id="b")],
        budget_for=_budget,
    )
    assert [r.model for r in results] == ["a", "b"]
    assert results[0].status == "ok"
    assert results[1].status == "reasoning_only"
