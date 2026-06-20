# ramcheck/gui/bundles.py
"""Read-only discovery of runs/: classify each dir and (for judged bundles) recompute the
verdict via the shared scorecard math. bundle.json carries neither status nor verdict."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ramcheck.gui import control


@dataclass
class BundleSummary:
    run_dir: Path
    status: str  # running | crashed | eval-only | judged
    pack_id: str = ""
    models: list[str] = field(default_factory=list)
    date: str = ""
    recommendation: str | None = None  # only when judged
    safety_passed: bool | None = None


def classify(run_dir: Path) -> BundleSummary | None:
    """Classify one dir; return None for legacy run/embed dirs (no bundle.json + no sentinel)."""
    sentinel = control.read_sentinel(run_dir)
    has_bundle = (run_dir / "bundle.json").exists()
    has_scores = (run_dir / "scores.csv").exists()
    has_responses = (run_dir / "responses.jsonl").exists()

    if sentinel is not None and control.is_active(sentinel):
        return _summary(run_dir, "running", sentinel)
    if sentinel is not None and not has_bundle:
        # sentinel present, pid dead, never finalized → crashed/resumable
        return _summary(run_dir, "crashed", sentinel)
    if not has_bundle:
        return None  # legacy run/embed dir
    if has_scores:
        return _judged_summary(run_dir)
    if has_responses:
        return _summary(run_dir, "eval-only", sentinel)
    return _summary(run_dir, "eval-only", sentinel)


def _manifest(run_dir: Path) -> dict[str, Any]:
    p = run_dir / "bundle.json"
    if not p.exists():
        return {}
    try:
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _summary(run_dir: Path, status: str, sentinel: dict[str, Any] | None) -> BundleSummary:
    m = _manifest(run_dir)
    return BundleSummary(
        run_dir=run_dir,
        status=status,
        pack_id=str(m.get("pack_id", sentinel.get("kind", "") if sentinel else "")),
        models=[mm["id"] for mm in m.get("models", [])],
        date=str(m.get("date", "")),
    )


def _judged_summary(run_dir: Path) -> BundleSummary:
    base = _summary(run_dir, "judged", None)
    rec, passed = _recompute_verdict(run_dir)
    base.recommendation = rec
    base.safety_passed = passed
    return base


def _recompute_verdict(run_dir: Path) -> tuple[str | None, bool | None]:
    """Recompute the (best) verdict via the shared scorecard.master_rows."""
    from ramcheck import scorecard
    from ramcheck.judge import load_judgements_jsonl
    from ramcheck.pack import load_pack
    from ramcheck.qualrun import load_responses_jsonl

    m = _manifest(run_dir)
    pack_path = m.get("pack_path")
    if not pack_path or not Path(pack_path).exists():
        return None, None
    pk = load_pack(pack_path)
    responses = load_responses_jsonl(run_dir / "responses.jsonl")
    verdicts = load_judgements_jsonl(run_dir / "judgements.jsonl")
    # master dims need a holistic ModelReport; v1 reads it back via re-judge-free path:
    # if reports aren't persisted, derive an empty-report fallback → recommendation None.
    rows = scorecard.master_rows(pk, responses, verdicts, _reports_from_scores(run_dir, pk))
    if not rows:
        return None, None
    # pick the strongest recommendation for the badge
    order = {"Ja": 3, "Mit Einschränkung": 2, "Nein": 1}
    best = max(rows, key=lambda r: order.get(str(r["recommendation"]), 0))
    return str(best["recommendation"]), bool(best["safety_passed"])


def _reports_from_scores(run_dir: Path, pk: Any) -> list[Any]:
    """Reconstruct ModelReport.dim_scores from scores.csv (metric_type='dimension' rows)."""
    import csv

    from ramcheck.results import ModelReport

    p = run_dir / "scores.csv"
    if not p.exists():
        return []
    by: dict[tuple[str, str], dict[str, int]] = {}
    with p.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("metric_type") != "dimension":
                continue
            key = (row["model"], row["variant"])
            by.setdefault(key, {})[row["metric"]] = int(float(row["score"]))
    return [ModelReport(m, v, dims, {}) for (m, v), dims in by.items()]


def discover(runs_dir: Path) -> list[BundleSummary]:
    if not runs_dir.exists():
        return []
    out: list[BundleSummary] = []
    for child in sorted(runs_dir.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        s = classify(child)
        if s is not None:
            out.append(s)
    return out
