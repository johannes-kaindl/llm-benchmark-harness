"""Judge view for the live monitor — the scoring counterpart to events.py.

The judge loop fires callbacks (on_judge_start / on_verdict / master / on_judge_done);
the CLI's --web wiring turns them into lines of an append-only judge_events.jsonl. The
monitor subprocess reads those lines back and aggregates them with build_view(). Pure:
no I/O beyond (de)serialising dicts. Unlike the eval view it does NOT tail resources.jsonl
(the judge runs on a different endpoint; the bundle's resources.jsonl is the old eval run).
"""

from __future__ import annotations

import json

JUDGE_START = "judge_start"
VERDICT = "verdict"
MASTER = "master"
JUDGE_DONE = "judge_done"

TAILS_RESOURCES = False
RATIONALE_MAX = 160


def judge_start_event(ts: float, total: int) -> dict[str, object]:
    return {"ts": ts, "type": JUDGE_START, "total": total}


def verdict_event(
    ts: float,
    i: int,
    model: str,
    variant: str,
    prompt_id: str,
    repeat: int,
    category: str,
    score: int,
    red_flag: bool,
    unscored: bool,
    rationale: str,
) -> dict[str, object]:
    return {
        "ts": ts,
        "type": VERDICT,
        "i": i,
        "model": model,
        "variant": variant,
        "prompt_id": prompt_id,
        "repeat": repeat,
        "category": category,
        "score": score,
        "red_flag": red_flag,
        "unscored": unscored,
        "rationale": rationale[:RATIONALE_MAX],
    }


def master_event(
    ts: float,
    model: str,
    variant: str,
    pct: float,
    safety_passed: bool,
    safety_reason: str,
    recommendation: str,
) -> dict[str, object]:
    return {
        "ts": ts,
        "type": MASTER,
        "model": model,
        "variant": variant,
        "pct": pct,
        "safety_passed": safety_passed,
        "safety_reason": safety_reason,
        "recommendation": recommendation,
    }


def judge_done_event(ts: float, total: int, scored: int) -> dict[str, object]:
    return {"ts": ts, "type": JUDGE_DONE, "total": total, "scored": scored}


def dumps(event: dict[str, object]) -> str:
    return json.dumps(event, ensure_ascii=False)


def parse_line(line: str) -> dict[str, object] | None:
    """Parse one judge_events.jsonl line; None on empty/partial/malformed lines."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict) or "type" not in obj:
        return None
    return obj
