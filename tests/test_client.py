from types import SimpleNamespace

from ramcheck.client import OpenAIStreamClient


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
