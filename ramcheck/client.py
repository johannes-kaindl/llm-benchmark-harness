"""Concrete OpenAI-compatible streaming client.

The only engine-aware code in the harness, and it stays thin: it adapts the
OpenAI SDK's streaming chunks to the engine-agnostic ``StreamEvent`` the runner
consumes. Works unchanged against LM Studio, mlx_lm.server and mlx-openai-server.
"""

from __future__ import annotations

from collections.abc import Iterator

from ramcheck.runner import StreamEvent


class OpenAIStreamClient:
    engine: str
    engine_version: str

    def __init__(
        self,
        base_url: str,
        api_key: str = "not-needed",
        *,
        engine: str = "openai-compat",
        engine_version: str = "unknown",
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.engine = engine
        self.engine_version = engine_version

    def stream(
        self,
        *,
        messages: list[dict[str, object]],
        model: str,
        max_tokens: int,
        temperature: float,
        seed: int,
    ) -> Iterator[StreamEvent]:
        # Our engine-agnostic message dicts don't match the SDK's TypedDict overloads;
        # the server-side schema is what actually validates them.
        stream = self._client.chat.completions.create(  # type: ignore[call-overload]
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if choices:
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta else None
                if content:
                    yield StreamEvent(delta_text=content)
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                yield StreamEvent(
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                )

    def embed(self, *, model: str, inputs: list[str]) -> int:
        """Embed a batch; returns the number of vectors produced."""
        resp = self._client.embeddings.create(model=model, input=inputs)
        return len(resp.data)
