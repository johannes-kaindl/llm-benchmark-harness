from pathlib import Path

from scripts.check_doc_links import broken_links


def test_curated_docs_have_no_broken_relative_links():
    # Check the curated docs (decisions/, reference/, explanation/, the hero). The dated
    # superpowers/ specs+plans are historical SDD artifacts and out of scope for this check.
    bad = [b for b in broken_links(Path("docs")) if not b.startswith("superpowers/")]
    assert bad == [], "broken doc links:\n" + "\n".join(bad)
