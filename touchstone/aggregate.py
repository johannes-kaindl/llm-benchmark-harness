"""Cross-run / cross-machine aggregation of scores.csv → one Hardware×Quality table.

The quality analogue of report.py (which aggregates raw.csv into report.md). Pure:
reads scores.csv rows, groups by (chip, ram_gb, pack, pack_version, model, quant, variant),
computes a weighted quality % (scale-max 5 — the 1..5 pack convention), and renders
Markdown + a concatenated scores_all.csv. Reuses no engine code; stdlib csv only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

SCALE_MAX = 5  # packs use a 1..5 scale; scores.csv doesn't carry it, so assume the convention

# (chip, ram_gb, pack, pack_version, model, quant, variant)
GroupKey = tuple[str, str, str, str, str, str, str]


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
    pack: str
    pack_version: str
    model: str
    quant: str
    variant: str
    quality_pct: float | None
    n_dims: int
    ttft_p50: str
    decode_med: str
    e2e_med: str
    peak_ram_gb: str
    model_delta_gb: str
    power: str
    dim_scores: dict[str, int] = field(default_factory=dict)


def _group_key(row: dict[str, str]) -> GroupKey:
    return (
        row.get("chip", ""),
        row.get("ram_gb", ""),
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


def _weighted_quality(rows: list[dict[str, str]]) -> tuple[float | None, dict[str, int]]:
    """Σ score·weight / Σ 5·weight over metric_type=='dimension' rows → (quality_pct, dim_scores).

    Returns (None, {}) when there are no scoreable dimension rows.
    """
    dim_scores: dict[str, int] = {}
    wsum = wmax = 0
    for r in rows:
        if r.get("metric_type") != "dimension":
            continue
        score = _as_int(r.get("score", ""))
        if score is None:
            continue
        weight = _as_int(r.get("weight", "")) or 0
        dim_scores[r.get("metric", "")] = score
        wsum += score * weight
        wmax += SCALE_MAX * weight
    return ((wsum / wmax * 100.0) if wmax else None), dim_scores


@dataclass
class PoolRow:
    id: str
    run_name: str
    chip: str
    ram_gb: str
    pack: str
    pack_version: str
    model: str
    quant: str
    variant: str
    quality_pct: float | None
    ttft_p50: str
    decode_med: str
    peak_ram_gb: str
    model_delta_gb: str
    power: str


def pool_rows(runs_dir: str | Path) -> list[PoolRow]:
    """One row per (run_name, model, variant) — NOT averaged across bundles (unlike
    aggregate()), so two runs of the same setup stay distinct. id = run_name|model|variant."""
    base = Path(runs_dir)
    if not base.exists():
        return []
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    order: list[tuple[str, str, str]] = []
    for f in sorted(base.rglob("scores.csv")):
        run_name = f.parent.name
        for r in load_scores_csv(f):
            k = (run_name, r.get("model", ""), r.get("variant", ""))
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(r)
    out: list[PoolRow] = []
    for run_name, model, variant in order:
        grp = groups[(run_name, model, variant)]
        first = grp[0]
        quality, _dims = _weighted_quality(grp)
        out.append(
            PoolRow(
                id=f"{run_name}|{model}|{variant}",
                run_name=run_name,
                chip=first.get("chip", ""),
                ram_gb=first.get("ram_gb", ""),
                pack=first.get("pack", ""),
                pack_version=first.get("pack_version", ""),
                model=model,
                quant=first.get("quant", ""),
                variant=variant,
                quality_pct=quality,
                ttft_p50=first.get("ttft_p50", ""),
                decode_med=first.get("decode_med", ""),
                peak_ram_gb=first.get("peak_ram_gb", ""),
                model_delta_gb=first.get("model_delta_gb", ""),
                power=first.get("power", ""),
            )
        )
    return out


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
        quality, dim_scores = _weighted_quality(grp)
        out.append(
            AggRow(
                chip=k[0],
                ram_gb=k[1],
                pack=k[2],
                pack_version=k[3],
                model=k[4],
                quant=k[5],
                variant=k[6],
                quality_pct=quality,
                n_dims=len(dim_scores),
                ttft_p50=first.get("ttft_p50", ""),
                decode_med=first.get("decode_med", ""),
                e2e_med=first.get("e2e_med", ""),
                peak_ram_gb=first.get("peak_ram_gb", ""),
                model_delta_gb=first.get("model_delta_gb", ""),
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
    lines.append("# touchstone — Cross-Run Aggregat (Hardware × Qualität)")
    lines.append("")
    if date_str:
        lines.append(f"> **Datum:** {date_str}")
        lines.append("")
    lines.append(
        "| Chip | RAM | Modell | Quant | Variante | Pack | Qualität % | "
        "TTFT P50 (s) | Decode (tok/s) | System-Peak (GB) | Modell-Delta (GB) | Power |"
    )
    lines.append("|---|---|---|---|---|---|:-:|:-:|:-:|:-:|:-:|:-:|")
    for a in agg:
        q = f"{a.quality_pct:.1f}" if a.quality_pct is not None else "—"
        pack_cell = (a.pack or "—") + (f" v{a.pack_version}" if a.pack_version else "")
        lines.append(
            f"| {a.chip or '—'} | {a.ram_gb or '—'} | {a.model or '—'} | "
            f"{a.quant or '—'} | {a.variant or '—'} | {pack_cell} | {q} | "
            f"{a.ttft_p50 or '—'} | {a.decode_med or '—'} | {a.peak_ram_gb or '—'} | "
            f"{a.model_delta_gb or '—'} | {a.power or '—'} |"
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
