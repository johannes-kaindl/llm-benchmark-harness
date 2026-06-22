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
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        from openai import NOT_GIVEN, OpenAI

        # Pass timeout=NOT_GIVEN (the SDK's "use default" sentinel) rather than None — None would
        # mean an *infinite* wait and silently break every existing eval/judge caller that relies
        # on the SDK's 600s read timeout. max_retries is a plain int (no sentinel), so omit it
        # entirely unless set; discovery passes max_retries=0 so the 3s timeout is the true bound.
        _timeout = NOT_GIVEN if timeout is None else timeout
        if max_retries is None:
            self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=_timeout)
        else:
            self._client = OpenAI(
                base_url=base_url, api_key=api_key, timeout=_timeout, max_retries=max_retries
            )
        self.engine = engine
        self.engine_version = engine_version

    def list_models(self) -> list[str]:
        """Model ids the endpoint advertises at /v1/models (for the GUI picker dropdown)."""
        return [m.id for m in self._client.models.list().data]

    def stream(
        self,
        *,
        messages: list[dict[str, object]],
        model: str,
        max_tokens: int,
        temperature: float,
        seed: int,
        extra_body: dict[str, object] | None = None,
    ) -> Iterator[StreamEvent]:
        # Our engine-agnostic message dicts don't match the SDK's TypedDict overloads;
        # the server-side schema is what actually validates them.
        # Forward extra_body ONLY when set — an unconditional extra_body=None would not hurt the
        # SDK but keeps the call clean and the spy-tested contract honest.
        extra = {"extra_body": extra_body} if extra_body else {}
        stream = self._client.chat.completions.create(  # type: ignore[call-overload]
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            stream=True,
            stream_options={"include_usage": True},
            **extra,
        )
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if choices:
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta else None
                # "Thinking" models (LM Studio / mlx / DeepSeek-style) put their reasoning
                # on a separate field — capture it instead of dropping it on the floor.
                reasoning = None
                if delta is not None:
                    reasoning = getattr(delta, "reasoning_content", None) or getattr(
                        delta, "reasoning", None
                    )
                if content:
                    yield StreamEvent(delta_text=content)
                if reasoning:
                    yield StreamEvent(reasoning_text=reasoning)
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
