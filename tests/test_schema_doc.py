"""The documented raw.csv columns must be exactly RAW_CSV_COLUMNS (CORE-META-18).

The list in docs/reference/metrics-and-schema.md had already drifted once: it lacked
`sys_used_delta_mb` (the cross-machine RAM number) long after the column shipped.
"""

from __future__ import annotations

import re
from pathlib import Path

from touchstone.models import RAW_CSV_COLUMNS

DOC = Path(__file__).resolve().parent.parent / "docs" / "reference" / "metrics-and-schema.md"


def test_documented_raw_csv_columns_match_the_schema() -> None:
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## `raw.csv`", 1)[1]
    block = re.search(r"```\n(.*?)```", section, re.S)
    assert block, "no code block under the raw.csv heading"
    documented = [c.strip() for c in block.group(1).replace("\n", " ").split(",") if c.strip()]
    assert documented == RAW_CSV_COLUMNS
