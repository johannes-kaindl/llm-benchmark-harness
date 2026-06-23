from ramcheck.gui.glossary import GLOSSARY, describe


def test_every_entry_has_term_short_long():
    for key, g in GLOSSARY.items():
        assert g.term and g.short and g.long, f"incomplete glossary entry: {key}"


def test_describe_known_key_returns_short():
    assert "Token" in describe("ttft_p50").short or "TTFT" in describe("ttft_p50").term


def test_describe_unknown_key_returns_safe_placeholder():
    d = describe("does_not_exist")
    assert d.term == "does_not_exist"
    assert d.short == ""
