"""Cross-judge aggregate (sub-project 2): discover per-bundle judge_quality.md results,
parse their YAML frontmatter, and (in group_by_pack) compute per-metric winners within a
pack. Pure + server-free (no fastapi/jinja) — mirrors gui/meta_report.py / aggregate.py."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PACK_UNKNOWN = "(Pack unbekannt — vor SP2 erzeugt, neu einlesen)"


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Parse a leading ``---\\n…\\n---`` YAML frontmatter block. Returns the mapping, or
    None if there is no frontmatter / it is not a mapping / the YAML is malformed. Never raises."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _as_float(v: Any) -> float | None:
    """A numeric frontmatter value → float; missing/null/non-numeric → None."""
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


@dataclass
class JudgeQualityRow:
    bundle: str
    pack: str
    judge_model: str
    mean_abs_delta: float | None
    names_improvement_rate: float | None
    cites_evidence_rate: float | None
    justifies_level_rate: float | None
    catches_safety_rate: float | None

    @property
    def key(self) -> str:
        return f"{self.bundle}|{self.judge_model}"


def judge_quality_rows(runs_dir: Path) -> list[JudgeQualityRow]:
    """Discover every judge_quality.md under runs_dir, parse its frontmatter into a row.
    Skips trashed bundles, files without judge_quality frontmatter, and unreadable files."""
    rows: list[JudgeQualityRow] = []
    for p in sorted(runs_dir.rglob("judge_quality.md")):
        if ".trash" in p.parts:
            continue  # a deleted bundle must not appear in the comparison
        try:
            fm = parse_frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm is None or fm.get("type") != "judge_quality":
            continue
        rows.append(
            JudgeQualityRow(
                bundle=str(fm.get("bundle") or p.parent.name),
                pack=str(fm.get("pack") or PACK_UNKNOWN),
                judge_model=str(fm.get("judge_model") or "—"),
                mean_abs_delta=_as_float(fm.get("mean_abs_delta")),
                names_improvement_rate=_as_float(fm.get("names_improvement_rate")),
                cites_evidence_rate=_as_float(fm.get("cites_evidence_rate")),
                justifies_level_rate=_as_float(fm.get("justifies_level_rate")),
                catches_safety_rate=_as_float(fm.get("catches_safety_rate")),
            )
        )
    rows.sort(key=lambda r: (r.pack, r.bundle, r.judge_model))
    return rows
