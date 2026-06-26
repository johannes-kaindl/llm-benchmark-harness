"""Pure renderers for the cross-run Meta-Report (sub-project E): compose the report_md
section helpers + an aggregate diff into ONE Obsidian Markdown document over N selected
cells, plus a leaderboard CSV. Pure (PoolRows + detail dicts → str), unit-testable.
"""

from __future__ import annotations

from typing import Any

from touchstone.aggregate import PoolRow


def select_rows(pool: list[PoolRow], rows: list[str] | None) -> list[PoolRow]:
    """The PoolRows whose id is in `rows`, in `rows` order (mirrors the /compare selection)."""
    wanted = [x for x in (rows or []) if x]
    by_id = {r.id: r for r in pool}
    return [by_id[i] for i in wanted if i in by_id]


def filter_detail_to_cells(detail: dict[str, Any], cells: set[tuple[str, str]]) -> dict[str, Any]:
    """A shallow copy of `detail` with the cell-keyed lists narrowed to `cells`
    ((model, variant) pairs); pack/manifest/perf/run_dir/etc. pass through unchanged."""
    out = dict(detail)
    out["responses"] = [r for r in detail.get("responses") or [] if (r.model, r.variant) in cells]
    out["verdicts"] = [v for v in detail.get("verdicts") or [] if (v.model, v.variant) in cells]
    out["reports"] = [r for r in detail.get("reports") or [] if (r.model, r.variant) in cells]
    out["master_rows"] = [
        row for row in detail.get("master_rows") or [] if (row["model"], row["variant"]) in cells
    ]
    out["cited_ids"] = {
        k: v
        for k, v in (detail.get("cited_ids") or {}).items()
        if (k.split("|", 2)[0], k.split("|", 2)[1]) in cells
    }
    return out
