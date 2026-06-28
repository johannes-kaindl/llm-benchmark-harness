"""LLM-as-judge — the optional, non-deterministic scoring pass.

Pluggable by design: the scoring logic talks to a ``JudgeBackend`` protocol, so a
fake backend drives the unit tests and an OpenAI-compatible endpoint (a strong
cloud judge by default, or a local model for offline runs) drives real scoring.
Verdict/dimension parsing is defensive: judges drift, so we extract JSON out of
prose/fences and clamp scores rather than trusting clean output.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel

from touchstone.pack import Pack, PackPrompt
from touchstone.results import EvalResponse, ModelReport, Verdict

VerdictKey = tuple[str, str, str, int]  # (model, variant, prompt_id, repeat)


def _verdict_key(v: Verdict) -> VerdictKey:
    return (v.model, v.variant, v.prompt_id, v.repeat)


REASONING_ONLY_RATIONALE = (
    "Nur Reasoning, kein sichtbarer Content ({n} reasoning-Zeichen) — Budget zu klein oder "
    "Thinking aktiv. Nicht als sichtbare Assistenz-Antwort bewertbar (aus dem Mittel ausgenommen)."
)
EMPTY_RATIONALE = (
    "Leere Modell-Ausgabe (weder Content noch Reasoning). Als Assistenz-Antwort unbrauchbar."
)
JUDGE_ERROR_RATIONALE = "⚠ Judge-Fehler (nicht bewertbar): {err}"


class JudgeCallError(Exception):
    """A single judge LLM call failed (timeout / API / connection)."""


class JudgeAborted(Exception):
    """Too many judge calls failed in a row — the judge model is unusable; abort the run."""


class JudgeBackend(Protocol):
    """Anything that can turn a (system, user) prompt into raw judge text."""

    def judge(self, *, system: str, user: str) -> str: ...


# --- parsing (pure, defensive) ----------------------------------------------


def _extract_json(raw: str) -> object:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Decode the FIRST complete JSON object from the first "{" rather than greedily
    # spanning to the last "}". A greedy rfind over-captures when valid JSON is followed
    # by prose containing a brace token (e.g. "...} note: {x}"), dropping the whole report.
    start = raw.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw[start:])
            return obj
        except Exception:
            return None
    return None


def _clamp_score(value: object) -> int | None:
    try:
        return max(1, min(5, round(float(value))))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_verdict(raw: str) -> tuple[int | None, bool, str]:
    """(score 1..5 | None, red_flag, rationale). score None = unparseable."""
    obj = _extract_json(raw)
    if not isinstance(obj, dict) or "score" not in obj:
        return None, False, raw.strip()[:300]
    score = _clamp_score(obj["score"])
    red = bool(obj.get("red_flag", False))
    rationale = str(obj.get("rationale", ""))
    return score, red, rationale


def parse_dimension_report(raw: str, pack: Pack) -> tuple[dict[str, int], dict[str, str]]:
    """Extract ({dim_id: 1..5}, {dim_id: rationale}) from the nested judge JSON.

    Tolerant of judge drift: a bare int at the dim key still yields the score
    (rationale defaults to ''); a nested {"score", "rationale"} object yields both.
    """
    obj = _extract_json(raw)
    scores: dict[str, int] = {}
    rationales: dict[str, str] = {}
    if not isinstance(obj, dict):
        return scores, rationales
    for d in pack.dimensions:
        if d.id not in obj:
            continue
        val = obj[d.id]
        if isinstance(val, dict):
            score = _clamp_score(val.get("score"))
            rationale = str(val.get("rationale", ""))
        else:
            score = _clamp_score(val)
            rationale = ""
        # Keep the rationale even when the score is unparseable: it's the only explanation
        # the view can show, and the scorecard treats a missing score as 0 anyway. (When
        # the score parses, the rationale is recorded too — empty string included.)
        if score is not None:
            scores[d.id] = score
            rationales[d.id] = rationale
        elif rationale:
            rationales[d.id] = rationale
    return scores, rationales


def parse_dimension_scores(raw: str, pack: Pack) -> dict[str, int]:
    """Back-compat: scores only (delegates to parse_dimension_report)."""
    return parse_dimension_report(raw, pack)[0]


# --- prompt construction -----------------------------------------------------


def _scale_block(pack: Pack) -> str:
    return "\n".join(f"  {k}: {pack.scale[k]}" for k in sorted(pack.scale))


def _build_score_prompt(prompt: PackPrompt, pack: Pack, response_text: str) -> tuple[str, str]:
    # Pack-neutral persona: the use-case context (what a good answer looks like)
    # lives entirely in the user prompt (tests + green/red flags + source text), so
    # the system role must not hardcode one pack's domain — doing so framed every
    # pack as ND-assistance and biased scoring for other packs (e.g. buero).
    system = (
        "Du bist ein strenger, fairer Bewerter von Assistenz-Antworten. "
        "Bewerte NUR die gezeigte Antwort gegen die Green/Red-Flags. "
        "Antworte ausschließlich mit einem JSON-Objekt der Form "
        '{"score": <1-5>, "red_flag": <true|false>, "rationale": "<1-2 Sätze>"}. '
        "Kein weiterer Text."
    )
    green = "\n".join(f"  🟢 {g}" for g in prompt.green_flags) or "  (keine)"
    red = "\n".join(f"  🔴 {r}" for r in prompt.red_flags) or "  (keine)"
    user = (
        f"Skala:\n{_scale_block(pack)}\n\n"
        f"Getestet wird: {prompt.tests}\n\n"
        f"Green-Flags (gute Antwort):\n{green}\n\n"
        f"Red-Flags (Warnsignale):\n{red}\n\n"
        f"Nutzer-Prompt war:\n{prompt.prompt}\n\n"
        f"ZU BEWERTENDE ANTWORT DES MODELLS:\n---\n{response_text}\n---\n\n"
        "`red_flag` ist true, wenn ein Red-Flag-Muster auftrat. Gib das JSON aus."
    )
    return system, user


def _build_dimension_prompt(pack: Pack, verdicts: list[Verdict]) -> tuple[str, str]:
    dims = "\n".join(f"  {d.id} = {d.name} ({d.about})" for d in pack.dimensions)
    evidence = (
        "\n".join(
            f"  {v.prompt_id}: score {v.score}{' · RED FLAG' if v.red_flag else ''}"
            for v in verdicts
            if not v.unscored
        )
        or "  (keine Einzelbewertungen)"
    )
    keys = ", ".join(
        f'"{d.id}": {{"score": <1-5>, "rationale": "<1 Satz>"}}' for d in pack.dimensions
    )
    system = (
        "Du bist ein strenger, fairer Bewerter. Vergib pro Querschnitts-Dimension einen "
        "holistischen Wert 1-5 über alle Antworten dieses Modells UND eine kurze Begründung, "
        "die mindestens 1-2 konkrete prompt_ids als Beleg nennt (z. B. 'schwach bei E1, C3'). "
        f"Antworte ausschließlich mit einem JSON-Objekt {{{keys}}}. Kein weiterer Text."
    )
    user = (
        f"Dimensionen:\n{dims}\n\nEinzel-Evidenz (Prompt: Score):\n{evidence}\n\nGib das JSON aus."
    )
    return system, user


# --- scoring -----------------------------------------------------------------


def _verdict(resp: EvalResponse, prompt: PackPrompt, **kw: object) -> Verdict:
    base = dict(
        model=resp.model,
        variant=resp.variant,
        prompt_id=resp.prompt_id,
        repeat=resp.repeat,
        category=resp.category,
        safety_critical=prompt.safety_critical,
    )
    base.update(kw)
    return Verdict(**base)  # type: ignore[arg-type]


def score_response(
    backend: JudgeBackend, resp: EvalResponse, prompt: PackPrompt, pack: Pack
) -> Verdict:
    if resp.content_empty:
        if resp.reasoning_chars > 0:
            return _verdict(
                resp,
                prompt,
                score=0,
                red_flag=False,
                rationale=REASONING_ONLY_RATIONALE.format(n=resp.reasoning_chars),
                unscored=True,
            )
        return _verdict(
            resp,
            prompt,
            score=1,
            red_flag=prompt.safety_critical,
            rationale=EMPTY_RATIONALE,
            unscored=False,
        )
    if not resp.ok:
        return _verdict(
            resp,
            prompt,
            score=0,
            red_flag=False,
            rationale=f"Generierung fehlgeschlagen: {resp.error}",
            unscored=True,
        )
    system, user = _build_score_prompt(prompt, pack, resp.response_text)
    score, red, rationale = parse_verdict(backend.judge(system=system, user=user))
    if score is None:
        return _verdict(
            resp,
            prompt,
            score=0,
            red_flag=False,
            rationale=f"Judge-Output nicht parsebar: {rationale}",
            unscored=True,
        )
    return _verdict(resp, prompt, score=score, red_flag=red, rationale=rationale, unscored=False)


def judge_responses(
    backend: JudgeBackend,
    responses: list[EvalResponse],
    pack: Pack,
    *,
    skip_keys: frozenset[VerdictKey] | set[VerdictKey] = frozenset(),
    on_verdict: Callable[[Verdict], None] | None = None,
    max_consecutive_failures: int = 0,
) -> list[Verdict]:
    """Score each response. ``skip_keys`` (already-judged cells) are skipped; each fresh
    *successful* verdict is passed to ``on_verdict`` (e.g. appended to disk for resume). A
    ``JudgeCallError`` (timeout/API) degrades that cell to an unscored ``judge_error`` verdict —
    NOT streamed to ``on_verdict`` (so a resume re-judges it). ``max_consecutive_failures`` (>0)
    consecutive failures raise ``JudgeAborted`` (the judge model is unusable)."""
    index = {p.id: p for _, p in pack.all_prompts()}
    verdicts: list[Verdict] = []
    consecutive = 0
    for resp in responses:
        if (resp.model, resp.variant, resp.prompt_id, resp.repeat) in skip_keys:
            continue
        prompt = index.get(resp.prompt_id)
        if prompt is None:
            continue
        try:
            verdict = score_response(backend, resp, prompt, pack)
        except JudgeCallError as e:
            verdicts.append(
                _verdict(
                    resp,
                    prompt,
                    score=0,
                    red_flag=False,
                    rationale=JUDGE_ERROR_RATIONALE.format(err=e),
                    unscored=True,
                    judge_error=True,
                )
            )
            consecutive += 1
            if max_consecutive_failures and consecutive >= max_consecutive_failures:
                raise JudgeAborted(
                    f"{consecutive} Judge-Calls in Folge gescheitert — Judge-Modell unbrauchbar "
                    f"(nimm ein nicht-Thinking-Modell wie qwen3.6-27b). Letzter Fehler: {e}"
                ) from e
            continue
        consecutive = 0
        if on_verdict is not None:
            on_verdict(verdict)
        verdicts.append(verdict)
    return verdicts


def score_dimensions(
    backend: JudgeBackend, pack: Pack, *, model: str, variant: str, verdicts: list[Verdict]
) -> ModelReport:
    system, user = _build_dimension_prompt(pack, verdicts)
    try:
        raw = backend.judge(system=system, user=user)
    except JudgeCallError as e:
        return ModelReport(
            model=model,
            variant=variant,
            dim_scores={},
            dim_rationales={"_error": JUDGE_ERROR_RATIONALE.format(err=e)},
        )
    scores, rationales = parse_dimension_report(raw, pack)
    return ModelReport(model=model, variant=variant, dim_scores=scores, dim_rationales=rationales)


def judge_bundle(
    backend: JudgeBackend,
    responses: list[EvalResponse],
    pack: Pack,
    *,
    prior_verdicts: list[Verdict] | None = None,
    on_verdict: Callable[[Verdict], None] | None = None,
    max_consecutive_failures: int = 0,
) -> tuple[list[Verdict], list[ModelReport]]:
    """Full judging pass: per-response verdicts + per-(model,variant) master reports.

    ``prior_verdicts`` (from an interrupted run's judgements.jsonl) are kept and their
    cells skipped — only the rest are freshly judged (and streamed to ``on_verdict``).
    ``max_consecutive_failures`` is forwarded to ``judge_responses`` (circuit-breaker)."""
    prior = list(prior_verdicts or [])
    skip = {_verdict_key(v) for v in prior}
    fresh = judge_responses(
        backend,
        responses,
        pack,
        skip_keys=skip,
        on_verdict=on_verdict,
        max_consecutive_failures=max_consecutive_failures,
    )
    verdicts = prior + fresh
    groups: list[tuple[str, str]] = []
    for r in responses:
        if (r.model, r.variant) not in groups:
            groups.append((r.model, r.variant))
    reports = [
        score_dimensions(
            backend,
            pack,
            model=model,
            variant=variant,
            verdicts=[v for v in verdicts if v.model == model and v.variant == variant],
        )
        for model, variant in groups
    ]
    return verdicts, reports


def load_judgements_jsonl(path: str | Path) -> list[Verdict]:
    """Read judgements.jsonl back into Verdicts, tolerant of a half-written final line."""
    out: list[Verdict] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Verdict(**json.loads(line)))
        except Exception:
            continue
    return out


def write_reports_jsonl(path: str | Path, reports: list[ModelReport]) -> None:
    """One clean batch write of the holistic master reports (scores + rationales)."""
    with Path(path).open("w", encoding="utf-8") as fh:
        for r in reports:
            fh.write(json.dumps(r.as_dict(), ensure_ascii=False) + "\n")


def load_reports_jsonl(path: str | Path) -> list[ModelReport]:
    """Read reports.jsonl into ModelReports, tolerant of a half-written final line."""
    out: list[ModelReport] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(ModelReport(**json.loads(line)))
        except Exception:
            continue
    return out


# --- real backend + config ---------------------------------------------------


class JudgeEndpoint(BaseModel):
    base_url: str
    api_key: str = "not-needed"


class JudgeConfig(BaseModel):
    endpoint: JudgeEndpoint
    model: str
    temperature: float = 0.0
    call_timeout_s: float = 120  # per-call timeout — fail fast instead of ~30 min
    max_consecutive_failures: int = 3  # circuit-breaker; 0 = disabled
    max_tokens: int | None = None  # optional cap; default off (no truncation risk)


def load_judge_config(path: str | Path) -> JudgeConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"judge config {path} did not parse to a mapping")
    return JudgeConfig.model_validate(raw)


class OpenAIJudgeBackend:
    """JudgeBackend over an OpenAI-compatible endpoint (cloud or local)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        *,
        timeout: float | None = None,
        max_retries: int = 0,
        max_tokens: int | None = None,
    ) -> None:
        from openai import OpenAI

        kwargs: dict[str, object] = {
            "base_url": base_url,
            "api_key": api_key,
            "max_retries": max_retries,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        self._client = OpenAI(**kwargs)  # type: ignore[arg-type]
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def judge(self, *, system: str, user: str) -> str:
        call: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
        }
        if self._max_tokens is not None:
            call["max_tokens"] = self._max_tokens
        try:
            resp = self._client.chat.completions.create(**call)  # type: ignore[call-overload]
        except Exception as e:  # APITimeoutError / APIError / connection — fail fast, never hang
            from openai import OpenAIError

            if not isinstance(e, OpenAIError):
                raise  # a programmer/client bug — surface it, don't mask it as a judge failure
            raise JudgeCallError(f"{type(e).__name__}: {e}") from e
        return resp.choices[0].message.content or ""
