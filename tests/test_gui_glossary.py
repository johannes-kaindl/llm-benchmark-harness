from touchstone.gui.glossary import GLOSSARY, describe


def test_every_entry_has_term_short_long():
    for key, g in GLOSSARY.items():
        assert g.term and g.short and g.long, f"incomplete glossary entry: {key}"


def test_describe_known_key_returns_short():
    assert "Token" in describe("ttft_p50").short or "TTFT" in describe("ttft_p50").term


def test_describe_unknown_key_returns_safe_placeholder():
    d = describe("does_not_exist")
    assert d.term == "does_not_exist"
    assert d.short == ""


def test_every_template_metric_key_is_in_glossary():
    # The self-explaining-UI invariant: every metric key a template renders via the
    # ui.metric / ui.mlabel / g(...) helpers must be defined in GLOSSARY, so a typo can
    # never ship an empty tooltip silently.
    import re
    from pathlib import Path

    tdir = Path("touchstone/gui/templates")
    patterns = [
        re.compile(r"""mlabel\(\s*["']([a-z_]+)["']"""),
        re.compile(r"""metric\([^,]+,\s*["']([a-z_]+)["']"""),
        re.compile(r"""\bg\(\s*["']([a-z_]+)["']"""),
    ]
    missing: dict[str, set[str]] = {}
    for html in tdir.rglob("*.html"):
        text = html.read_text(encoding="utf-8")
        for pat in patterns:
            for key in pat.findall(text):
                if key not in GLOSSARY:
                    missing.setdefault(html.name, set()).add(key)
    assert not missing, f"template metric keys missing from GLOSSARY: {missing}"
