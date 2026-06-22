from __future__ import annotations

import types

from ramcheck.client import OpenAIStreamClient


def test_list_models_returns_ids():
    c = OpenAIStreamClient("http://localhost:1/v1")  # OpenAI() is lazy, no connection
    c._client = types.SimpleNamespace(
        models=types.SimpleNamespace(
            list=lambda: types.SimpleNamespace(
                data=[types.SimpleNamespace(id="a"), types.SimpleNamespace(id="b")]
            )
        )
    )
    assert c.list_models() == ["a", "b"]


def test_constructor_accepts_timeout():
    c = OpenAIStreamClient("http://localhost:1/v1", timeout=3.0)
    assert c._client is not None


def test_omitting_timeout_preserves_sdk_default():
    # Passing timeout=None to OpenAI() would mean "no timeout" (infinite wait); omitting it
    # keeps the SDK's 600s default, which existing eval/judge callers depend on.
    c = OpenAIStreamClient("http://localhost:1/v1")
    assert c._client.timeout is not None
    assert c._client.max_retries == 2  # SDK default preserved when not overridden


def test_discovery_client_options_applied():
    c = OpenAIStreamClient("http://localhost:1/v1", timeout=3.0, max_retries=0)
    assert c._client.max_retries == 0  # discovery fails fast (no retries stretching the 3s)
