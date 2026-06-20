import math

from ramcheck.config import Config
from ramcheck.runner import (
    StreamEvent,
    derive_rates,
    iter_cells,
    resolve_engine,
    stream_once,
)


class SeqClock:
    """Returns each value once, then repeats the last — safe for any call count."""

    def __init__(self, vals):
        self.vals = list(vals)
        self.i = 0

    def __call__(self):
        v = self.vals[self.i]
        if self.i < len(self.vals) - 1:
            self.i += 1
        return v


class FakeClient:
    engine = "fake"
    engine_version = "0.0"

    def __init__(self, deltas, prompt_tokens, completion_tokens):
        self.deltas = deltas
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    def stream(self, **kwargs):
        for d in self.deltas:
            yield StreamEvent(delta_text=d)
        yield StreamEvent(
            prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens
        )


def test_stream_once_derives_ttft_and_rates():
    client = FakeClient(["Hallo", " Welt"], prompt_tokens=1000, completion_tokens=100)
    clock = SeqClock([0.0, 0.5, 2.0])  # t0=0, first-delta=0.5, end=2.0
    wall = SeqClock([100.0, 102.0])
    out = stream_once(
        client,
        messages=[],
        model="m",
        max_tokens=100,
        temperature=0.0,
        seed=42,
        clock=clock,
        wall=wall,
    )
    assert out.ttft_s == 0.5
    assert out.e2e_s == 2.0
    assert out.prompt_tokens == 1000
    assert out.completion_tokens == 100
    assert out.text == "Hallo Welt"
    assert out.t_start == 100.0 and out.t_end == 102.0

    prefill, decode = derive_rates(out)
    assert math.isclose(prefill, 2000.0)  # 1000 / 0.5
    assert math.isclose(decode, 100 / 1.5)  # 100 / (2.0 - 0.5)


def test_stream_once_handles_missing_usage_with_heuristic():
    # No usage event → completion tokens estimated from text length.
    class NoUsage(FakeClient):
        def stream(self, **kwargs):
            for d in self.deltas:
                yield StreamEvent(delta_text=d)

    client = NoUsage(["a" * 40], prompt_tokens=0, completion_tokens=0)
    out = stream_once(
        client,
        messages=[],
        model="m",
        max_tokens=10,
        temperature=0.0,
        seed=1,
        clock=SeqClock([0.0, 0.1, 1.0]),
        wall=SeqClock([0.0, 1.0]),
    )
    assert out.completion_tokens > 0  # heuristic kicked in


def test_stream_once_error_is_captured_not_raised():
    class Boom:
        engine = "x"
        engine_version = "0"

        def stream(self, **kwargs):
            raise RuntimeError("connection refused")
            yield  # pragma: no cover

    out = stream_once(
        Boom(),
        messages=[],
        model="m",
        max_tokens=10,
        temperature=0.0,
        seed=1,
        clock=SeqClock([0.0, 1.0]),
        wall=SeqClock([0.0, 1.0]),
    )
    assert out.ok is False
    assert "connection refused" in out.error


def test_stream_once_captures_reasoning_separately():
    class ReasoningClient:
        engine = "x"
        engine_version = "0"

        def stream(self, **kwargs):
            yield StreamEvent(reasoning_text="let me think ")
            yield StreamEvent(reasoning_text="hard")
            yield StreamEvent(delta_text="Answer")
            yield StreamEvent(prompt_tokens=5, completion_tokens=2)

    out = stream_once(
        ReasoningClient(),
        messages=[],
        model="m",
        max_tokens=10,
        temperature=0.0,
        seed=1,
        clock=SeqClock([0.0, 0.3, 1.0]),
        wall=SeqClock([0.0, 1.0]),
    )
    assert out.text == "Answer"
    assert out.reasoning_text == "let me think hard"
    assert out.ttft_s == 0.3  # TTFT is content-based, reasoning doesn't trigger it


def test_stream_once_reasoning_only_leaves_content_empty():
    class ThinkOnly:
        engine = "x"
        engine_version = "0"

        def stream(self, **kwargs):
            yield StreamEvent(reasoning_text="thinking...")
            yield StreamEvent(prompt_tokens=3, completion_tokens=0)

    out = stream_once(
        ThinkOnly(),
        messages=[],
        model="m",
        max_tokens=10,
        temperature=0.0,
        seed=1,
        clock=SeqClock([0.0, 1.0]),
        wall=SeqClock([0.0, 1.0]),
    )
    assert out.text == ""
    assert out.reasoning_text == "thinking..."
    assert math.isnan(out.ttft_s)  # no content token → no content TTFT


def test_resolve_engine_by_port():
    assert resolve_engine("http://localhost:1234/v1") == "lm-studio"
    assert resolve_engine("http://localhost:8080/v1") == "mlx"
    assert resolve_engine("http://localhost:8000/v1") == "mlx"
    assert resolve_engine("http://example/v1") == "openai-compat"


def _cfg(scenarios, buckets=(4096, 16384)):
    return Config.model_validate(
        {
            "endpoint": {"base_url": "http://localhost:1234/v1"},
            "machine": "M1",
            "context_buckets": list(buckets),
            "scenarios": scenarios,
            "models": [{"id": "qwen3-8b", "quant": "Q5"}],
        }
    )


def test_iter_cells_context_free_scenarios_get_one_cell():
    cfg = _cfg(["bodydouble", "compose"])
    cells = iter_cells(cfg)
    assert len(cells) == 2
    assert all(c.target_ctx == 0 for c in cells)


def test_iter_cells_context_sensitive_expand_buckets():
    cfg = _cfg(["rag_synth"], buckets=(4096, 16384, 32768))
    cells = iter_cells(cfg)
    assert [c.target_ctx for c in cells] == [4096, 16384, 32768]


def test_iter_cells_uses_scenario_default_max_tokens():
    cfg = _cfg(["bodydouble"])
    cells = iter_cells(cfg)
    assert cells[0].max_tokens == 150  # DEFAULT_MAX_TOKENS["bodydouble"]
