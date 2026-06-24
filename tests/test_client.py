from types import SimpleNamespace

from touchstone.client import OpenAIStreamClient


def test_client_yields_reasoning_and_content_from_delta(monkeypatch):
    client = OpenAIStreamClient("http://localhost:9/v1")
    # chunk 1: only reasoning_content; chunk 2: only content; chunk 3: usage
    chunks = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content="think"))
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="hi"))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
        ),
    ]
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kw: chunks)

    evs = list(client.stream(messages=[], model="m", max_tokens=1, temperature=0.0, seed=0))
    assert any(e.reasoning_text == "think" for e in evs)
    assert any(e.delta_text == "hi" for e in evs)
    assert any(e.completion_tokens == 1 for e in evs)


def test_client_supports_reasoning_field_alias(monkeypatch):
    client = OpenAIStreamClient("http://localhost:9/v1")
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning="hmm"))],
        usage=None,
    )
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kw: [chunk])
    evs = list(client.stream(messages=[], model="m", max_tokens=1, temperature=0.0, seed=0))
    assert any(e.reasoning_text == "hmm" for e in evs)


def test_stream_forwards_extra_body_only_when_set(monkeypatch):
    from touchstone.client import OpenAIStreamClient

    captured = {}

    class _FakeCreate:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return iter([])  # empty stream

    class _FakeOpenAI:
        def __init__(self, **_):
            self.chat = type("C", (), {"completions": type("X", (), {"create": _FakeCreate()})()})()
            self.models = None

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    c = OpenAIStreamClient("http://x/v1")

    list(
        c.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="m",
            max_tokens=10,
            temperature=0.0,
            seed=42,
        )
    )
    assert "extra_body" not in captured  # not set → not forwarded (no SDK default override)

    captured.clear()
    list(
        c.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="m",
            max_tokens=10,
            temperature=0.0,
            seed=42,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    )
    assert captured["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_stream_omits_max_tokens_when_none(monkeypatch):
    from touchstone.client import OpenAIStreamClient

    captured = {}

    class _FakeCreate:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return iter([])  # empty stream

    class _FakeOpenAI:
        def __init__(self, **_):
            self.chat = type("C", (), {"completions": type("X", (), {"create": _FakeCreate()})()})()
            self.models = None

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    c = OpenAIStreamClient("http://x/v1")

    # max_tokens=None → no cap sent → the server answers freely (eval default)
    list(
        c.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="m",
            max_tokens=None,
            temperature=0.0,
            seed=42,
        )
    )
    assert "max_tokens" not in captured

    captured.clear()
    list(
        c.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="m",
            max_tokens=256,
            temperature=0.0,
            seed=42,
        )
    )
    assert captured["max_tokens"] == 256
