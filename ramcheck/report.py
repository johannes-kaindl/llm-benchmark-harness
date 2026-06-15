"""Report writer: raw.csv (every request) + report.md (SSOT columns).

raw.csv keeps everything — warmup, throttled, battery, failed — because the brief
says keep the raw rows. report.md aggregates per cell and *excludes* warmup,
throttled, battery and failed runs from the numbers (those are still counted in
the Throttle/Akku column). Cold-start TTFT is reported on its own line.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

from ramcheck import stats
from ramcheck.models import RAW_CSV_COLUMNS, RunRecord, pressure_max

# Cell identity = one row of the SSOT table.
CellKey = tuple[str, str, str, str, int]  # machine, model, quant, scenario, target_ctx

# Brief's validity floor: N>=8 per cell → >=7 valued after discarding one warmup.
# A cell can also drop below this if too many runs were throttled/battery/failed, so
# the floor is checked on the *actual* valid count, not just the configured runs.
MIN_VALID_RUNS = 7


@dataclass
class CellAggregate:
    machine: str
    model: str
    quant: str
    scenario: str
    target_ctx: int
    actual_ctx: int
    ttft_p50: float
    ttft_p95: float
    decode_median: float
    prefill_median: float
    peak_rss_mb: float | None
    mem_pressure_max: str
    swap_delta_mb: float
    cv_pct: float
    n_valid: int
    n_excluded_throttled: int
    n_excluded_battery: int
    n_total: int
    low_n: bool  # n_valid below the statistical floor → numbers are under-powered


# --- raw.csv -----------------------------------------------------------------


def write_raw_csv(records: list[RunRecord], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RAW_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(r.as_dict())


def _coerce(value: str, kind: type) -> object:
    if value == "" or value == "None":
        return None
    if kind is bool:
        return value.strip().lower() in {"true", "1", "yes"}
    if kind is int:
        return int(float(value))
    if kind is float:
        return float(value)
    return value


def load_raw_csv(path: str | Path) -> list[RunRecord]:
    """Inverse of write_raw_csv — used by `ramcheck report` to regenerate report.md."""
    from dataclasses import fields

    field_types = {f.name: f.type for f in fields(RunRecord)}
    out: list[RunRecord] = []
    with Path(path).open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            kwargs: dict[str, object] = {}
            for col in RAW_CSV_COLUMNS:
                if col not in row:
                    continue
                raw = row[col]
                t = field_types.get(col, str)
                if t in (int, "int"):
                    kwargs[col] = _coerce(raw, int) if raw not in ("", "None") else 0
                elif t in (float, "float"):
                    kwargs[col] = _coerce(raw, float)
                elif t in (bool, "bool"):
                    kwargs[col] = _coerce(raw, bool)
                elif col in {"peak_rss_mb", "sys_used_mb", "swap_delta_mb"}:
                    kwargs[col] = _coerce(raw, float)
                else:
                    kwargs[col] = raw
            # t_start/t_end are internal timing fields, intentionally not in the CSV.
            # The merge already ran, so report regeneration doesn't need them.
            kwargs.setdefault("t_start", 0.0)
            kwargs.setdefault("t_end", 0.0)
            out.append(RunRecord(**kwargs))  # type: ignore[arg-type]
    return out


# --- aggregation -------------------------------------------------------------


def _cell_key(r: RunRecord) -> CellKey:
    return (r.machine, r.model, r.quant, r.scenario, r.target_ctx)


def is_valid_for_aggregate(r: RunRecord) -> bool:
    """Counts toward the reported numbers? Excludes warmup/cold/failed/throttled/battery."""
    if r.warmup or r.is_cold_start or not r.ok:
        return False
    return not (r.throttled or r.power_source == "battery")


def aggregate_cells(records: list[RunRecord]) -> list[CellAggregate]:
    groups: dict[CellKey, list[RunRecord]] = {}
    for r in records:
        if r.is_cold_start:
            continue  # cold-start reported separately
        groups.setdefault(_cell_key(r), []).append(r)

    cells: list[CellAggregate] = []
    for key, group in sorted(groups.items()):
        valid = [r for r in group if is_valid_for_aggregate(r)]
        non_warmup = [r for r in group if not r.warmup and r.ok]
        ttfts = [r.ttft_s for r in valid]
        peak_rss = [r.peak_rss_mb for r in valid if r.peak_rss_mb is not None]
        cells.append(
            CellAggregate(
                machine=key[0],
                model=key[1],
                quant=key[2],
                scenario=key[3],
                target_ctx=key[4],
                actual_ctx=int(stats.median([r.actual_prompt_tokens for r in valid]))
                if valid
                else 0,
                ttft_p50=stats.percentile(ttfts, 50),
                ttft_p95=stats.percentile(ttfts, 95),
                decode_median=stats.median([r.decode_tps for r in valid]),
                prefill_median=stats.median([r.prefill_tps for r in valid]),
                peak_rss_mb=max(peak_rss) if peak_rss else None,
                mem_pressure_max=pressure_max(
                    [r.mem_pressure_max for r in valid if r.mem_pressure_max]
                ),
                swap_delta_mb=max(
                    (r.swap_delta_mb for r in valid if r.swap_delta_mb is not None), default=0.0
                ),
                cv_pct=stats.cv_percent(ttfts),
                n_valid=len(valid),
                n_excluded_throttled=sum(1 for r in non_warmup if r.throttled),
                n_excluded_battery=sum(
                    1 for r in non_warmup if r.power_source == "battery" and not r.throttled
                ),
                n_total=len(group),
                low_n=len(valid) < MIN_VALID_RUNS,
            )
        )
    return cells


# --- rendering ---------------------------------------------------------------


def _fnum(x: float, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{digits}f}"


def _rss(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x / 1024:.1f} GB"


def _engine_line(records: list[RunRecord]) -> str:
    pairs = sorted({(r.engine, r.engine_version) for r in records if r.engine})
    return ", ".join(f"{e} {v}".strip() for e, v in pairs) or "unknown"


def render_report_md(
    records: list[RunRecord],
    *,
    date_str: str,
    host: dict[str, str] | None = None,
) -> str:
    host = host or {}
    cells = aggregate_cells(records)
    cold = [r for r in records if r.is_cold_start]
    power_sources = sorted({r.power_source for r in records if r.power_source})

    lines: list[str] = []
    lines.append("# ramcheck — Benchmark-Report")
    lines.append("")
    lines.append("> [!info] Lauf-Metadaten")
    lines.append(f"> **Datum:** {date_str}  ")
    lines.append(
        f"> **macOS:** {host.get('macos', 'unknown')} · **Chip:** {host.get('chip', 'unknown')} · **RAM:** {host.get('ram_gb', 'unknown')}  "
    )
    lines.append(f"> **Engine:** {_engine_line(records)}  ")
    lines.append(f"> **Power-Source:** {', '.join(power_sources) or 'unknown'}")
    lines.append("")

    # Cold-start section.
    if cold:
        lines.append("## ❄️ Cold-Start (separat, einmalig)")
        lines.append("")
        lines.append("| Maschine | Modell | Szenario | Cold-TTFT (s) |")
        lines.append("|---|---|---|---|")
        for r in cold:
            lines.append(f"| {r.machine} | {r.model} | {r.scenario} | {_fnum(r.ttft_s)} |")
        lines.append("")

    # Main SSOT table.
    lines.append("## 📊 Ergebnis-Tabelle")
    lines.append("")
    lines.append(
        "| Datum | Maschine | Modell | Quant | Kontext (ist) | TTFT P50/P95 (s) | "
        "Decode (tok/s) | Prefill (tok/s) | Peak-RAM / Druck / Swap | Qual. | Flow | "
        "Konsist. (CV%) | Throttle/Akku |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in cells:
        ttft = f"{_fnum(c.ttft_p50)} / {_fnum(c.ttft_p95)}"
        ram = (
            f"{_rss(c.peak_rss_mb)} / {c.mem_pressure_max or '—'} / {_fnum(c.swap_delta_mb, 0)} MB"
        )
        excl_parts = []
        if c.n_excluded_throttled:
            excl_parts.append(f"{c.n_excluded_throttled}× throttled")
        if c.n_excluded_battery:
            excl_parts.append(f"{c.n_excluded_battery}× Akku")
        if c.low_n:
            excl_parts.append(f"⚠️ n={c.n_valid}")
        excl = ", ".join(excl_parts) if excl_parts else "—"
        ctx = (
            f"{c.actual_ctx} (nativ)"
            if c.target_ctx == 0
            else f"{c.actual_ctx} (Ziel {c.target_ctx})"
        )
        lines.append(
            f"| {date_str} | {c.machine} | {c.model} | {c.quant} | "
            f"{ctx} | {ttft} | {_fnum(c.decode_median, 1)} | "
            f"{_fnum(c.prefill_median, 1)} | {ram} | | | {_fnum(c.cv_pct, 1)} | {excl} |"
        )
    lines.append("")
    lines.append(
        "> _`Qual.` und `Flow` bleiben zur manuellen Bewertung leer. "
        "`Konsist.` = CV% der TTFT (Stdev/Mittel). Kontext „ist“ = Median der echten "
        "`prompt_tokens` aus `usage`. Aggregate schließen Warmup-, Cold-, throttled- und "
        f"Akku-Läufe aus (roh in `raw.csv` erhalten). ⚠️ n=<k> markiert Zellen mit weniger "
        f"als {MIN_VALID_RUNS} gewerteten Läufen — die Zahlen sind dort unterbesetzt._"
    )
    lines.append("")
    return "\n".join(lines)


def write_report(
    records: list[RunRecord],
    out_dir: str | Path,
    *,
    date_str: str,
    host: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw_path = out / "raw.csv"
    md_path = out / "report.md"
    write_raw_csv(records, raw_path)
    md_path.write_text(render_report_md(records, date_str=date_str, host=host), encoding="utf-8")
    return md_path, raw_path
