import pytest

from ramcheck import prompts
from ramcheck.prompts import HeuristicCounter


def test_heuristic_counter_monotonic():
    c = HeuristicCounter(chars_per_token=4.0)
    assert c.count("aaaa") == 1
    assert c.count("a" * 40) == 10


@pytest.mark.parametrize("target", [256, 1000, 4096, 16384])
def test_pad_to_tokens_lands_near_target(target):
    c = HeuristicCounter(chars_per_token=4.0)
    text = prompts.pad_to_tokens(target, c)
    n = c.count(text)
    # Trimmed under target but not wildly short.
    assert n <= target
    assert n >= target * 0.85


def test_pad_to_tokens_deterministic():
    c = HeuristicCounter()
    assert prompts.pad_to_tokens(2000, c) == prompts.pad_to_tokens(2000, c)


def test_pad_zero_is_empty():
    assert prompts.pad_to_tokens(0, HeuristicCounter()) == ""


def test_build_bodydouble_cycles_variants():
    c = HeuristicCounter()
    m0 = prompts.build_messages("bodydouble", target_ctx=0, counter=c, variant_index=0)
    m1 = prompts.build_messages("bodydouble", target_ctx=0, counter=c, variant_index=1)
    assert m0[0]["content"] != m1[0]["content"]
    assert m0[0]["role"] == "user"


def test_build_rag_synth_has_source_instruction():
    c = HeuristicCounter()
    msgs = prompts.build_messages("rag_synth", target_ctx=4096, counter=c)
    content = msgs[0]["content"]
    assert "Quelle" in content


def test_build_vlm_multimodal_content_array(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    msgs = prompts.build_messages(
        "vlm", target_ctx=0, counter=HeuristicCounter(), vlm_image_path=img
    )
    content = msgs[0]["content"]
    assert isinstance(content, list)
    types = {part["type"] for part in content}
    assert types == {"text", "image_url"}
    url = next(p["image_url"]["url"] for p in content if p["type"] == "image_url")
    assert url.startswith("data:image/png;base64,")


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        prompts.build_messages("nope", target_ctx=0, counter=HeuristicCounter())


def test_vlm_requires_image():
    with pytest.raises(ValueError):
        prompts.build_messages("vlm", target_ctx=0, counter=HeuristicCounter())
