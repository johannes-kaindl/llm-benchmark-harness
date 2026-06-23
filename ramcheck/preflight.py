"""Pre-flight smoke: before the matrix, send ONE small request per model with that model's
EFFECTIVE budget and check whether visible content appears. Diagnostic only — never raises,
never measures (runs before the sampler). The harness's earliest warning that a model will
produce empty answers (e.g. a reasoning model whose budget is eaten by thinking)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ramcheck.config import ModelSpec
from ramcheck.runner import StreamClient, stream_once

SMOKE_PROMPT = "Antworte in genau einem Satz: Was ist 2 + 2?"

PreflightStatus = Literal["ok", "reasoning_only", "empty", "error"]


@dataclass
class PreflightResult:
    model: str
    status: PreflightStatus
    text_chars: int
    reasoning_chars: int
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "status": self.status,
            "text_chars": self.text_chars,
            "reasoning_chars": self.reasoning_chars,
            "detail": self.detail,
        }


def preflight_models(
    client: StreamClient,
    models: list[ModelSpec],
    *,
    budget_for: Callable[[ModelSpec], int | None],
    prompt: str = SMOKE_PROMPT,
) -> list[PreflightResult]:
    out: list[PreflightResult] = []
    msgs: list[dict[str, object]] = [{"role": "user", "content": prompt}]
    for m in models:
        try:
            outcome = stream_once(
                client,
                messages=msgs,
                model=m.id,
                max_tokens=budget_for(m),
                temperature=0.0,
                seed=42,
                extra_body=m.extra_body or None,
            )
        except Exception as e:  # defensive: smoke must never crash the run
            out.append(PreflightResult(m.id, "error", 0, 0, f"{type(e).__name__}: {e}"))
            continue
        tc = len(outcome.text.strip())
        rc = len(outcome.reasoning_text)
        if not outcome.ok:
            out.append(PreflightResult(m.id, "error", tc, rc, outcome.error))
        elif tc > 0:
            out.append(PreflightResult(m.id, "ok", tc, rc, ""))
        elif rc > 0:
            out.append(
                PreflightResult(
                    m.id,
                    "reasoning_only",
                    tc,
                    rc,
                    f"nur Reasoning ({rc} Zeichen), kein sichtbarer Content",
                )
            )
        else:
            out.append(PreflightResult(m.id, "empty", tc, rc, "leere Ausgabe"))
    return out
