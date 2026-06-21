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


def _load_reports(run_dir: Path, pk: Any) -> list[Any]:
    """Prefer reports.jsonl (carries dim_rationales); fall back to lossy scores.csv
    reconstruction (rationales empty → UI shows 'Begründung nicht erfasst')."""
    from ramcheck.judge import load_reports_jsonl

    reports = load_reports_jsonl(run_dir / "reports.jsonl")
    if reports:
        return reports
    return _reports_from_scores(run_dir, pk)


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
    rows = scorecard.master_rows(pk, responses, verdicts, _load_reports(run_dir, pk))
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
            # A judge that omitted a master dimension writes score='' — skip it
            # rather than crashing the whole overview (yields a valid partial report).
            raw = (row.get("score") or "").strip()
            if not raw:
                continue
            try:
                val = int(float(raw))
            except (TypeError, ValueError):
                continue
            key = (row["model"], row["variant"])
            by.setdefault(key, {})[row["metric"]] = val
    return [ModelReport(m, v, dims, {}) for (m, v), dims in by.items()]


def bundle_detail(run_dir: Path) -> dict[str, Any] | None:
    """Rich structure for the result view: pack + answers + verdicts + reports
    (reports.jsonl preferred) + the active K.-o. branch + cited prompt_ids."""
    from ramcheck import scorecard
    from ramcheck.judge import load_judgements_jsonl
    from ramcheck.merge import load_samples_jsonl
    from ramcheck.pack import load_pack
    from ramcheck.qualrun import load_responses_jsonl

    m = _manifest(run_dir)
    pack_path = m.get("pack_path")
    if not pack_path or not Path(pack_path).exists():
        return None
    pk = load_pack(pack_path)
    responses = load_responses_jsonl(run_dir / "responses.jsonl")
    verdicts = load_judgements_jsonl(run_dir / "judgements.jsonl")
    reports = _load_reports(run_dir, pk)
    rows = scorecard.master_rows(pk, responses, verdicts, reports)
    samples = load_samples_jsonl(run_dir / "resources.jsonl")
    known_ids = {p.id for _, p in pk.all_prompts()}
    return {
        "run_dir": run_dir,
        "manifest": m,
        "pack": pk,
        "responses": responses,
        "verdicts": verdicts,
        "reports": reports,
        "master_rows": rows,
        "ko": _ko_branches(pk, verdicts, rows),  # which branch fired + its root
        "cpu": [s.cpu_pct for s in samples],
        "ram": [s.sys_used_mb for s in samples],
        "cited_ids": _cited_prompt_ids(
            reports, known_ids
        ),  # {"model|variant|dim_id": [prompt_id,...]}
    }


def _cited_prompt_ids(reports: list[Any], known_ids: set[str]) -> dict[str, list[str]]:
    """Parse the prompt_ids the judge cited in each dim_rationale (match against pack ids).

    Keys are composite ``"model|variant|dim_id"`` strings so multi-model bundles stay
    unambiguous.  Shape: ``{"model|variant|dim_id": [prompt_id, ...]}``.
    """
    import re

    out: dict[str, list[str]] = {}
    for rep in reports:
        for dim_id, text in getattr(rep, "dim_rationales", {}).items():
            hits = [tok for tok in re.findall(r"[A-Za-z]\d+", text or "") if tok in known_ids]
            if hits:
                out[f"{rep.model}|{rep.variant}|{dim_id}"] = hits
    return out


def _ko_branches(pk: Any, verdicts: list[Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each failed-KO group, which of the two roots fired (dimension-floor / red-flag-prompt).

    Red-flag hits are computed per (model, variant) group — matching the scorecard math —
    so that a failure in one model's group never pollutes another's branch entry.
    """
    from ramcheck import scorecard

    branches: list[dict[str, Any]] = []
    for r in rows:
        if r.get("safety_passed"):
            continue
        model, variant = r["model"], r["variant"]
        gv = [v for v in verdicts if (v.model, v.variant) == (model, variant)]
        group_red_flagged = scorecard.red_flagged_prompts(gv)
        hit_prompts = [p for p in pk.ko_rule.red_flag_prompts if p in group_red_flagged]
        branches.append(
            {
                "model": model,
                "variant": variant,
                "dimension": pk.ko_rule.dimension,
                "threshold": pk.ko_rule.threshold,
                "red_flag_prompts": hit_prompts,  # non-empty → red-flag branch fired
            }
        )
    return branches


def discover(runs_dir: Path) -> list[BundleSummary]:
    if not runs_dir.exists():
        return []
    out: list[BundleSummary] = []
    for child in sorted(runs_dir.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        # Defensive: one corrupt bundle must never 500 the whole overview. A failed
        # classify is shown as an error row, not propagated up to the route.
        try:
            s = classify(child)
        except Exception:
            out.append(BundleSummary(run_dir=child, status="error"))
            continue
        if s is not None:
            out.append(s)
    return out
