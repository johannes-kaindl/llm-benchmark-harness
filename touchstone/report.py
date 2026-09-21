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

from touchstone import stats
from touchstone.models import RAW_CSV_COLUMNS, RunRecord, pressure_max

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
    peak_sys_used_mb: float | None  # peak SYSTEM memory used — the engine-agnostic RAM truth
    peak_rss_mb: float | None  # server-PID RSS (undercounts mmap'd weights on Apple Silicon)
    mem_pressure_max: str
    swap_delta_mb: float
    cv_pct: float
    n_valid: int
    n_excluded_throttled: int
    n_excluded_battery: int
    n_total: int
    low_n: bool  # n_valid below the statistical floor → numbers are under-powered
    # The FIRST request of the cell — the cache-cold one. On a prefix-caching engine the warm
    # aggregates above measure the cache, and this is the honest latency (nan if it failed).
    ttft_cold: float = math.nan
    prefill_cold: float = math.nan
    # Share of the prompt the engine reported as cached (usage.prompt_tokens_details), in %.
    # None = the engine doesn't report it — unknown, which is not the same as 0 %.
    cached_pct_warm: float | None = None
    cached_pct_cold: float | None = None
    # The cold request is the run's very first one → it also carries model loading, so its value
    # is not comparable with the other cells' cold values (it is kept, but marked).
    cold_includes_load: bool = False


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
    """Inverse of write_raw_csv — used by `touchstone report` to regenerate report.md."""
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
                # Annotations are strings (``from __future__ import annotations``), so match on
                # the declared type text. Optional numbers ("float | None", "int | None") are
                # recognised by type, not by a hand-kept name list: that list once missed
                # ``sys_used_delta_mb``, which then came back as the string '5000.0'.
                t = str(field_types.get(col, "str")).replace(" ", "")
                if t == "int":
                    kwargs[col] = _coerce(raw, int) if raw not in ("", "None") else 0
                elif t == "int|None":
                    kwargs[col] = _coerce(raw, int)
                elif t in ("float", "float|None"):
                    kwargs[col] = _coerce(raw, float)
                elif t == "bool":
                    kwargs[col] = _coerce(raw, bool)
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


def _first_requests(records: list[RunRecord]) -> dict[CellKey, RunRecord]:
    """The cache-cold request of each cell.

    The runner sends a cell's prompt first as the global cold-start (cell 0 only) or as the
    discarded warmup (every other cell); every later run repeats the SAME prompt and can hit the
    engine's prefix cache. For cell 0 the warmup follows the cold-start and is already warm, so
    the cold-start wins where there is one. Derived from the existing flags on purpose — a
    separate "first in cell" field would store the same truth twice.
    """
    firsts: dict[CellKey, RunRecord] = {}
    for r in records:
        if r.is_cold_start:
            firsts[_cell_key(r)] = r
    for r in records:
        if r.warmup:
            firsts.setdefault(_cell_key(r), r)
    return firsts


def _cached_pct(r: RunRecord) -> float | None:
    if r.cached_tokens is None or r.actual_prompt_tokens <= 0:
        return None
    return r.cached_tokens / r.actual_prompt_tokens * 100


def aggregate_cells(records: list[RunRecord]) -> list[CellAggregate]:
    firsts = _first_requests(records)
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
        peak_sys = [r.sys_used_mb for r in valid if r.sys_used_mb is not None]
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
                peak_sys_used_mb=max(peak_sys) if peak_sys else None,
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
        _set_cold_fields(cells[-1], firsts.get(key), valid)
    return cells


def _set_cold_fields(cell: CellAggregate, first: RunRecord | None, valid: list[RunRecord]) -> None:
    warm_pcts = [p for p in (_cached_pct(r) for r in valid) if p is not None]
    cell.cached_pct_warm = stats.median(warm_pcts) if warm_pcts else None
    # The cold request obeys the same exclusions as the warm runs (failed/throttled/battery);
    # only its warmup/cold-start flag is — by definition — expected.
    if first is None or not first.ok or first.throttled or first.power_source == "battery":
        return
    cell.ttft_cold = first.ttft_s
    cell.prefill_cold = first.prefill_tps
    cell.cached_pct_cold = _cached_pct(first)
    cell.cold_includes_load = first.is_cold_start


# --- rendering ---------------------------------------------------------------


def _fnum(x: float, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{digits}f}"


def _gb(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x / 1024:.1f} GB"


LOAD_MARK = "⁽ᴸ⁾"


def _cold_cell(c: CellAggregate) -> str:
    value = _fnum(c.ttft_cold)
    return f"{value} {LOAD_MARK}" if c.cold_includes_load and value != "—" else value


def _cache_cell(c: CellAggregate) -> str:
    if c.cached_pct_warm is None and c.cached_pct_cold is None:
        return "—"

    def side(p: float | None) -> str:
        return "—" if p is None else f"{p:.0f} %"

    return f"{side(c.cached_pct_warm)} / {side(c.cached_pct_cold)}"


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
    lines.append("# touchstone — Benchmark-Report")
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
        "| Datum | Maschine | Modell | Quant | Kontext (ist) | TTFT P50/P95 (s) | TTFT kalt (s) | "
        "Decode (tok/s) | Prefill (tok/s) | Cache warm/kalt | Peak-RAM / Druck / Swap | Qual. | "
        "Flow | Konsist. (CV%) | Throttle/Akku |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in cells:
        ttft = f"{_fnum(c.ttft_p50)} / {_fnum(c.ttft_p95)}"
        # System memory is the RAM truth (engine-agnostic); server RSS is a hint that
        # undercounts mmap'd weights on Apple Silicon, so it's shown in parentheses.
        ram = (
            f"{_gb(c.peak_sys_used_mb)} sys (RSS {_gb(c.peak_rss_mb)}) / "
            f"{c.mem_pressure_max or '—'} / {_fnum(c.swap_delta_mb, 0)} MB"
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
            f"{ctx} | {ttft} | {_cold_cell(c)} | {_fnum(c.decode_median, 1)} | "
            f"{_fnum(c.prefill_median, 1)} | {_cache_cell(c)} | {ram} | | | "
            f"{_fnum(c.cv_pct, 1)} | {excl} |"
        )
    lines.append("")
    lines.append(
        "> _`Qual.` und `Flow` bleiben zur manuellen Bewertung leer. "
        "`Konsist.` = CV% der TTFT (Stdev/Mittel). Kontext „ist“ = Median der echten "
        "`prompt_tokens` aus `usage`. **Peak-RAM** = Spitzen-System-Memory (engine-agnostisches "
        "RAM-Signal, maßgeblich für OOM/Druck); `RSS` = Server-PID-RSS, das auf Apple Silicon "
        "die mmap'ten Modellgewichte unterzählt — daher nur als Hinweis in Klammern. "
        "**TTFT kalt** = der erste Request je Zelle (Cold-Start bzw. Warmup) — auf einer Engine "
        "mit Prefix-Cache die ehrliche Latenz, denn die gewerteten Läufe wiederholen denselben "
        "Prompt und messen dort den Cache. **Cache warm/kalt** = Anteil des Prompts, den die "
        "Engine als gecacht meldet (`usage.prompt_tokens_details.cached_tokens`); `—` = die "
        "Engine meldet es nicht (unbekannt, nicht 0 %). Ein hoher Kalt-Anteil heißt: Die Zelle "
        "teilt ihren Anfang mit einer vorigen. "
        f"{LOAD_MARK} = dieser Wert ist der allererste Request des Laufs und enthält ggf. das "
        "Laden des Modells — nicht mit den Kalt-Werten der übrigen Zellen vergleichbar "
        "(gemessen an mlx_lm.server: kalt 6,42 s gegen 0,32 s warm in dieser Zelle, 3,85 gegen "
        "4,17 s in der nächsten). "
        "Aggregate schließen Warmup-, Cold-, throttled- und "
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
