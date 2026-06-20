"""Cross-run / cross-machine aggregation of scores.csv → one Hardware×Quality table.

The quality analogue of report.py (which aggregates raw.csv into report.md). Pure:
reads scores.csv rows, groups by (chip, machine, model, variant, pack), computes a
weighted quality % (scale-max 5 — the 1..5 pack convention), and renders Markdown +
a concatenated scores_all.csv. Reuses no engine code; stdlib csv only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

SCALE_MAX = 5  # packs use a 1..5 scale; scores.csv doesn't carry it, so assume the convention

# (chip, ram_gb, machine, pack, pack_version, model, quant, variant)
GroupKey = tuple[str, str, str, str, str, str, str, str]


def load_scores_csv(path: str | Path) -> list[dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_all_scores(runs_dir: str | Path) -> list[dict[str, str]]:
    """Every row from every scores.csv under runs_dir (recursive), concatenated."""
    base = Path(runs_dir)
    if not base.exists():
        return []
    rows: list[dict[str, str]] = []
    for f in sorted(base.rglob("scores.csv")):
        rows.extend(load_scores_csv(f))
    return rows


@dataclass
class AggRow:
    chip: str
    ram_gb: str
    machine: str
    pack: str
    pack_version: str
    model: str
    quant: str
    variant: str
    quality_pct: float | None
    n_dims: int
    ttft_p50: str
    decode_med: str
    peak_ram_gb: str
    power: str
    dim_scores: dict[str, int] = field(default_factory=dict)


def _group_key(row: dict[str, str]) -> GroupKey:
    return (
        row.get("chip", ""),
        row.get("ram_gb", ""),
        row.get("machine", ""),
        row.get("pack", ""),
        row.get("pack_version", ""),
        row.get("model", ""),
        row.get("quant", ""),
        row.get("variant", ""),
    )


def _as_int(s: str) -> int | None:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def aggregate(rows: list[dict[str, str]]) -> list[AggRow]:
    """Group score rows and compute a weighted quality % per hardware×model×variant."""
    groups: dict[GroupKey, list[dict[str, str]]] = {}
    order: list[GroupKey] = []
    for r in rows:
        k = _group_key(r)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)

    out: list[AggRow] = []
    for k in order:
        grp = groups[k]
        first = grp[0]
        dim_scores: dict[str, int] = {}
        wsum = 0
        wmax = 0
        for r in grp:
            if r.get("metric_type") != "dimension":
                continue
            score = _as_int(r.get("score", ""))
            if score is None:
                continue
            weight = _as_int(r.get("weight", "")) or 0
            dim_scores[r.get("metric", "")] = score
            wsum += score * weight
            wmax += SCALE_MAX * weight
        quality = (wsum / wmax * 100.0) if wmax else None
        out.append(
            AggRow(
                chip=k[0],
                ram_gb=k[1],
                machine=k[2],
                pack=k[3],
                pack_version=k[4],
                model=k[5],
                quant=k[6],
                variant=k[7],
                quality_pct=quality,
                n_dims=len(dim_scores),
                ttft_p50=first.get("ttft_p50", ""),
                decode_med=first.get("decode_med", ""),
                peak_ram_gb=first.get("peak_ram_gb", ""),
                power=first.get("power", ""),
                dim_scores=dim_scores,
            )
        )
    out.sort(
        key=lambda a: (a.chip, -(a.quality_pct if a.quality_pct is not None else float("-inf")))
    )
    return out


def render_aggregate_md(agg: list[AggRow], *, date_str: str = "") -> str:
    lines: list[str] = []
    lines.append("# ramcheck — Cross-Run Aggregat (Hardware × Qualität)")
    lines.append("")
    if date_str:
        lines.append(f"> **Datum:** {date_str}")
        lines.append("")
    lines.append(
        "| Chip | RAM | Maschine | Modell | Quant | Variante | Pack | Qualität % | "
        "TTFT P50 (s) | Decode (tok/s) | Peak-RAM (GB) | Power |"
    )
    lines.append("|---|---|---|---|---|---|---|:-:|:-:|:-:|:-:|:-:|")
    for a in agg:
        q = f"{a.quality_pct:.1f}" if a.quality_pct is not None else "—"
        lines.append(
            f"| {a.chip or '—'} | {a.ram_gb or '—'} | {a.machine or '—'} | {a.model} | "
            f"{a.quant or '—'} | {a.variant} | {a.pack} v{a.pack_version} | {q} | "
            f"{a.ttft_p50 or '—'} | {a.decode_med or '—'} | {a.peak_ram_gb or '—'} | "
            f"{a.power or '—'} |"
        )
    lines.append("")
    lines.append(
        "> _Qualität % = Σ(Score·Gewicht) / Σ(5·Gewicht); Skala-Max 5 (Pack-Konvention) "
        "angenommen, da `scores.csv` die Skala nicht trägt. Sicherheit/K.-o. steht pro Bundle in "
        "`scorecard.md` (nicht in `scores.csv`, daher hier nicht aggregiert). Roh-Zeilen in "
        "`scores_all.csv`._"
    )
    lines.append("")
    return "\n".join(lines)


def write_scores_all_csv(rows: list[dict[str, str]], path: str | Path) -> None:
    """Concatenate all score rows into one CSV (union of columns, first-seen order)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cols: list[str] = []
    for r in rows:
        for key in r:
            if key not in cols:
                cols.append(key)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
