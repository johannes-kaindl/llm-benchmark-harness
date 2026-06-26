"""Pure renderers for the cross-run Meta-Report (sub-project E): compose the report_md
section helpers + an aggregate diff into ONE Obsidian Markdown document over N selected
cells, plus a leaderboard CSV. Pure (PoolRows + detail dicts → str), unit-testable.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from typing import Any

from touchstone import aggregate
from touchstone.aggregate import PoolRow
from touchstone.gui import report_md
from touchstone.gui.glossary import Glossary


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
    # cited_ids keys are "model|variant|dim_id"; match by the exact "model|variant|" prefix
    # (str.startswith accepts a tuple) so a name containing "|" isn't mis-split by position.
    # (Residual ambiguity if two distinct cells share a "m|v|" prefix is the same project-wide
    # pipe-convention limitation as the run_name|model|variant id scheme — out of scope here.)
    cell_prefixes = tuple(f"{m}|{v}|" for m, v in cells)
    out["cited_ids"] = {
        k: v for k, v in (detail.get("cited_ids") or {}).items() if k.startswith(cell_prefixes)
    }
    return out


_CSV_COLUMNS = [
    "run_name",
    "model",
    "variant",
    "pack",
    "pack_version",
    "chip",
    "ram_gb",
    "quant",
    "quality_pct",
    "ttft_p50",
    "decode_med",
    "model_delta_gb",
    "peak_ram_gb",
    "power",
]


def render_meta_leaderboard_csv(selected: list[PoolRow]) -> str:
    """One row per selected cell — the analyst's spreadsheet artifact (always real data)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for r in selected:
        writer.writerow(
            {
                "run_name": r.run_name,
                "model": r.model,
                "variant": r.variant,
                "pack": r.pack,
                "pack_version": r.pack_version,
                "chip": r.chip,
                "ram_gb": r.ram_gb,
                "quant": r.quant,
                "quality_pct": "" if r.quality_pct is None else r.quality_pct,
                "ttft_p50": r.ttft_p50,
                "decode_med": r.decode_med,
                "model_delta_gb": r.model_delta_gb,
                "peak_ram_gb": r.peak_ram_gb,
                "power": r.power,
            }
        )
    return buf.getvalue()


_LEADER_METRICS = [  # (winner-key, label, PoolRow attr, is_quality)
    ("quality_pct", "Quality %", "quality_pct", True),
    ("decode_med", "Decode (tok/s)", "decode_med", False),
    ("ttft_p50", "TTFT P50 (s)", "ttft_p50", False),
    ("model_delta_gb", "Modell-Δ (GB)", "model_delta_gb", False),
    ("peak_ram_gb", "System-Peak (GB)", "peak_ram_gb", False),
]


def _num(s: Any) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _meta_frontmatter(selected: list[PoolRow], *, include_judging: bool) -> list[str]:
    fm: list[tuple[str, Any]] = [
        ("title", f"Meta-Report ({len(selected)} Zellen)"),
        ("type", "meta_report"),
        ("date", ""),
        ("n_cells", len(selected)),
        ("judging", "included" if include_judging else "bewertungs-auftrag"),
    ]
    out = ["---"]
    for k, v in fm:
        out.append(f"{k}: {report_md._yaml(v)}")
    for key, vals in (
        ("packs", sorted({r.pack for r in selected})),
        ("models", sorted({r.model for r in selected})),
        ("variants", sorted({r.variant for r in selected})),
        ("runs", sorted({r.run_name for r in selected})),
    ):
        out.append(f"{key}:")
        out.extend(f"  - {report_md._yaml(x)}" for x in vals)
    out.append("---")
    return out


def _cellval(r: PoolRow, attr: str) -> str:
    v = getattr(r, attr)
    if attr == "quality_pct":
        return "—" if v is None else f"{v:.0f}%"
    return str(v) if v not in (None, "") else "—"


def _summary_section(selected: list[PoolRow], *, include_judging: bool) -> list[str]:
    metrics = [m for m in _LEADER_METRICS if include_judging or not m[3]]  # drop quality if blank
    diff = aggregate.diff_rows(selected) if len(selected) >= 2 else None
    winners = diff.winners if diff else {}
    out = ["## Summary\n"]
    if diff and diff.common:
        out.append("**Gemeinsam:** " + " · ".join(f"{k}={v}" for k, v in diff.common if v) + "\n")
    out.append("| Modell·Variante | " + " | ".join(m[1] for m in metrics) + " |")
    out.append("|---|" + "|".join("---" for _ in metrics) + "|")
    if include_judging:
        rows = sorted(
            selected, key=lambda r: (r.quality_pct is not None, r.quality_pct or 0.0), reverse=True
        )
    else:
        rows = sorted(
            selected,
            key=lambda r: (_num(r.decode_med) is not None, _num(r.decode_med) or 0.0),
            reverse=True,
        )
    for r in rows:
        cells = []
        for key, _label, attr, _q in metrics:
            val = _cellval(r, attr)
            if winners.get(key) == r.id and val != "—":
                val += " 🏆"
            cells.append(val)
        out.append(f"| {r.model}·{r.variant} | " + " | ".join(cells) + " |")
    out.append("")
    return out


def _bundle_detail_section(
    detail: dict[str, Any], glossary: Mapping[str, Glossary], *, include_judging: bool, top: str
) -> list[str]:
    pack = detail["pack"]
    run_dir = detail.get("run_dir")
    run_name = run_dir.name if run_dir is not None else "bundle"
    manifest = detail.get("manifest") or {}
    host = manifest.get("host") or {}
    responses = detail.get("responses") or []
    verdicts = detail.get("verdicts") or []
    reports = detail.get("reports") or []
    master_rows = detail.get("master_rows") or []
    cited_ids = detail.get("cited_ids") or {}
    title_by = {p.id: p.title for _, p in pack.all_prompts()}
    known_ids = {p.id for _, p in pack.all_prompts()}
    chip = host.get("chip", "")
    out = [f"### Bundle {run_name} · {pack.title}" + (f" · {chip}" if chip else "") + "\n"]
    if include_judging:
        doc = report_md._load_doc(run_dir, pack, responses, verdicts, reports, host, manifest)
        cells_by = {(c.model, c.variant): c for c in doc.cells} if doc is not None else {}
        out.extend(
            report_md.section_master_scorecard(
                pack=pack,
                master_rows=master_rows,
                reports=reports,
                cited_ids=cited_ids,
                cells_by=cells_by,
                glossary=glossary,
                title_by=title_by,
                known_ids=known_ids,
            )
        )
    else:
        out.append(
            report_md._eval_task(
                pack,
                responses,
                lambda pid, d=None: report_md._prompt_link(pid, title_by, d),
                known_ids,
            )
        )
    out.extend(
        report_md.section_prompts_antworten(
            pack=pack,
            responses=responses,
            verdicts=verdicts if include_judging else [],
            glossary=glossary,
            title_by=title_by,
            top=top,
        )
    )
    return out


def render_meta_report_md(
    selected: list[PoolRow],
    details: list[dict[str, Any]],
    glossary: Mapping[str, Glossary],
    *,
    include_judging: bool,
) -> str:
    out = _meta_frontmatter(selected, include_judging=include_judging)
    w = out.append
    packs = sorted({r.pack for r in selected})
    w(f"\n# Meta-Report — {', '.join(packs) or '—'} · {len(selected)} Zellen\n")
    w("## Inhalt\n")
    for label in ["Summary", "Detail", "Bewertungs-Methode", "Metrik-Glossar"]:
        w(f"- {report_md._wl(label)}")
    w("")
    top = f"\n{report_md._wl('Inhalt', '↑ zum Inhalt')}\n"

    out.extend(_summary_section(selected, include_judging=include_judging))
    w(top)

    w("## Detail\n")
    # shared pack sections once per distinct pack (dimensions + prompt variants)
    seen: dict[str, Any] = {}
    for d in details:
        pk = d["pack"]
        seen.setdefault(pk.id, pk)
    for pk in seen.values():
        out.extend(report_md.section_dimensionen(pk))
        out.extend(report_md.section_prompt_varianten(pk, glossary))
    for d in details:
        out.extend(_bundle_detail_section(d, glossary, include_judging=include_judging, top=top))

    # Bewertungs-Methode once per distinct pack (static + that pack's K.-o./scale; no judge identity)
    for pk in seen.values():
        title_by = {p.id: p.title for _, p in pk.all_prompts()}
        known_ids = {p.id for _, p in pk.all_prompts()}
        out.extend(
            report_md.section_methode(
                pack=pk,
                include_judging=include_judging,
                judge={},
                reports=[],
                title_by=title_by,
                known_ids=known_ids,
            )
        )
    w(top)
    out.extend(report_md.section_glossar(glossary))
    w(top)
    return "\n".join(out) + "\n"
