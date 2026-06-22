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
