"""Check that every relative .md link under a docs root resolves to an existing file.

Pure stdlib; used by tests/test_doc_links.py and ad hoc (`python scripts/check_doc_links.py`).
Ignores http(s)/mailto and Obsidian-style [[wikilinks]] (only markdown [text](target) links are
checked). A link's target is resolved relative to the file that contains it.
"""

from __future__ import annotations

import re
from pathlib import Path

LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def broken_links(root: Path) -> list[str]:
    out: list[str] = []
    for md in sorted(root.rglob("*.md")):
        for m in LINK.finditer(md.read_text(encoding="utf-8")):
            target = m.group(1).split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (md.parent / target).resolve().exists():
                out.append(f"{md.relative_to(root)} → {target}")
    return out


if __name__ == "__main__":
    bad = broken_links(Path("docs"))
    print("\n".join(bad) if bad else "all doc links resolve")
    raise SystemExit(1 if bad else 0)
