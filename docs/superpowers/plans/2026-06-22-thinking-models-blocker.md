# Thinking-Modelle / leerer Content — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reasoning-Modelle (gemma-4-12b-qat & Co.), die alles ins `reasoning`-Feld schreiben, fair vermessbar und transparent bewertbar machen — plus Pre-Flight-Frühwarnung und Judge-Modell-Picker.

**Architecture:** Drei orthogonale Komponenten auf einem additiven Schema-Fundament. (A) `ModelSpec` bekommt opt-in `reasoning_headroom_tokens` + generisches `extra_body` (engine-agnostisch durchgereicht); (B) der Judge bewertet reasoning-only als `unscored` statt stiller 1/5; (C) ein Pre-Flight-Smoke vor der Matrix warnt früh; (D) der Judge bekommt denselben Endpoint-Modell-Picker wie die Eval. Mess-Bedingung wird nie heimlich verändert.

**Tech Stack:** Python 3.12 · uv · OpenAI SDK · pydantic · Typer · FastAPI/Jinja2/Alpine · pytest · ruff · mypy strict.

**Spec:** `docs/superpowers/specs/2026-06-22-thinking-models-blocker-design.md`

**Konventionen (gelten für JEDEN Task):**
- Nach jeder Änderung: `uv run pytest -q` (relevante Datei zuerst), `uv run ruff check . && uv run ruff format .`, `uv run mypy ramcheck/`.
- Commits klein, eine Aufgabe pro Commit. Commit-Trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- `mypy` prüft nur `ramcheck/` — Test-FakeClients müssen nicht protocol-konform sein.
- GUI-Tasks: Server nach jeder Änderung neu starten, JS headless verifizieren (Seiten mit offener SSE können NICHT per `--dump-dom` gesnapshottet werden → dort Server-SSE + Markup prüfen).

---

## Phase A — Schema + Durchreichung (Fundament)

### Task 1: `ModelSpec` — `reasoning_headroom_tokens` + `extra_body`

**Files:**
- Modify: `ramcheck/config.py:34-37` (`ModelSpec`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Failing test** — in `tests/test_config.py` ergänzen:

```python
def test_modelspec_thinking_defaults_are_neutral():
    from ramcheck.config import ModelSpec

    m = ModelSpec(id="x")
    assert m.reasoning_headroom_tokens == 0
    assert m.extra_body == {}


def test_modelspec_thinking_fields_roundtrip_through_models_json():
    from ramcheck.config import models_from_json

    specs = models_from_json(
        '[{"id": "gemma", "reasoning_headroom_tokens": 2000,'
        ' "extra_body": {"chat_template_kwargs": {"enable_thinking": false}}}]'
    )
    assert specs[0].reasoning_headroom_tokens == 2000
    assert specs[0].extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_config.py -k thinking -v`
Expected: FAIL (`ModelSpec` has no field `reasoning_headroom_tokens`).

- [ ] **Step 3: Implement** — `ramcheck/config.py`, `ModelSpec` erweitern:

```python
class ModelSpec(BaseModel):
    id: str
    quant: str = ""
    max_tokens_default: int = 400
    reasoning_headroom_tokens: int = 0  # extra TOTAL budget for THIS model so a thinker still
    # reaches visible content; the *visible* answer budget stays pack.prompt.max_tokens (fair compare)
    extra_body: dict[str, object] = Field(default_factory=dict)  # passed verbatim to the OpenAI
    # call (e.g. disable thinking) — engine-agnostic; the harness never branches on engine here
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_config.py -k thinking -v` → PASS. Dann `uv run mypy ramcheck/` (sauber, da additive Felder mit Defaults).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/config.py tests/test_config.py
git commit -m "feat(config): ModelSpec reasoning_headroom_tokens + extra_body (opt-in, default-neutral)"
```

---

### Task 2: `extra_body` durch `stream`/`stream_once` reichen

**Files:**
- Modify: `ramcheck/runner.py:43-55` (`StreamClient` Protocol), `ramcheck/runner.py:87-122` (`stream_once`)
- Modify: `ramcheck/client.py:49-68` (`OpenAIStreamClient.stream`)
- Test: `tests/test_client.py`, `tests/test_runner.py`

- [ ] **Step 1: Failing test (client)** — in `tests/test_client.py` ergänzen. Spy auf die SDK-`create`-Call-Kwargs:

```python
def test_stream_forwards_extra_body_only_when_set(monkeypatch):
    from ramcheck.client import OpenAIStreamClient

    captured = {}

    class _FakeCreate:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return iter([])  # empty stream

    class _FakeOpenAI:
        def __init__(self, **_):
            self.chat = type("C", (), {"completions": type("X", (), {"create": _FakeCreate()})()})()
            self.models = None

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    c = OpenAIStreamClient("http://x/v1")

    list(c.stream(messages=[{"role": "user", "content": "hi"}], model="m",
                  max_tokens=10, temperature=0.0, seed=42))
    assert "extra_body" not in captured  # not set → not forwarded (no SDK default override)

    captured.clear()
    list(c.stream(messages=[{"role": "user", "content": "hi"}], model="m",
                  max_tokens=10, temperature=0.0, seed=42,
                  extra_body={"chat_template_kwargs": {"enable_thinking": False}}))
    assert captured["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_client.py -k extra_body -v`
Expected: FAIL (`stream()` got an unexpected keyword argument `extra_body`).

- [ ] **Step 3: Implement** — `ramcheck/runner.py`, `StreamClient.stream` Signatur erweitern:

```python
    def stream(
        self,
        *,
        messages: list[dict[str, object]],
        model: str,
        max_tokens: int,
        temperature: float,
        seed: int,
        extra_body: dict[str, object] | None = None,
    ) -> Iterator[StreamEvent]: ...
```

`ramcheck/client.py`, `OpenAIStreamClient.stream`:

```python
    def stream(
        self,
        *,
        messages: list[dict[str, object]],
        model: str,
        max_tokens: int,
        temperature: float,
        seed: int,
        extra_body: dict[str, object] | None = None,
    ) -> Iterator[StreamEvent]:
        # Forward extra_body ONLY when set — an unconditional extra_body=None would not hurt the
        # SDK but keeps the call clean and the spy-tested contract honest.
        extra = {"extra_body": extra_body} if extra_body else {}
        stream = self._client.chat.completions.create(  # type: ignore[call-overload]
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            stream=True,
            stream_options={"include_usage": True},
            **extra,
        )
```

(Rest der Methode unverändert.)

`ramcheck/runner.py`, `stream_once` — Signatur + Forwarding. Nach `seed: int,` einfügen:

```python
    extra_body: dict[str, object] | None = None,
```

und den `client.stream(...)`-Aufruf (Zeile ~116) ändern zu:

```python
        for ev in client.stream(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            **({"extra_body": extra_body} if extra_body else {}),
        ):
```

(Conditional kwarg → bestehende Test-FakeClients ohne `extra_body`-Param bleiben grün, solange `extra_body` None ist.)

- [ ] **Step 4: Failing test (runner)** — in `tests/test_runner.py` ergänzen (FakeClient, der die kwargs fängt):

```python
def test_stream_once_forwards_extra_body():
    from ramcheck.runner import StreamEvent, stream_once

    seen = {}

    class _Spy:
        engine = "fake"
        engine_version = "0"

        def stream(self, **kwargs):
            seen.update(kwargs)
            yield StreamEvent(delta_text="ok")
            yield StreamEvent(prompt_tokens=1, completion_tokens=1)

    stream_once(_Spy(), messages=[{"role": "user", "content": "x"}], model="m",
                max_tokens=5, temperature=0.0, seed=42,
                extra_body={"foo": "bar"})
    assert seen["extra_body"] == {"foo": "bar"}
```

- [ ] **Step 5: Run both, expect PASS**

Run: `uv run pytest tests/test_client.py tests/test_runner.py -k extra_body -v` → PASS. Dann `uv run mypy ramcheck/`.

- [ ] **Step 6: Commit**

```bash
git add ramcheck/runner.py ramcheck/client.py tests/test_client.py tests/test_runner.py
git commit -m "feat(client): forward extra_body through stream/stream_once (engine-agnostic thinking switch)"
```

---

### Task 3: `EvalResponse.reasoning_text`

**Files:**
- Modify: `ramcheck/results.py:54` (nach `reasoning_chars`)
- Test: `tests/test_results.py` (oder, falls nicht vorhanden, in `tests/test_qualrun.py` mitabgedeckt durch Task 4)

- [ ] **Step 1: Failing test** — in `tests/test_results.py` ergänzen (falls Datei fehlt, neu anlegen mit Standard-Imports):

```python
def test_evalresponse_reasoning_text_defaults_empty():
    from ramcheck.results import EvalResponse

    r = EvalResponse(
        pack_id="p", pack_version=1, machine="M", model="m", quant="", engine="e",
        engine_version="0", variant="none", category="A", prompt_id="A1", repeat=0,
        response_text="", content_empty=True, ttft_s=0.0, decode_tps=0.0, prefill_tps=0.0,
        e2e_s=0.0, prompt_tokens=0, completion_tokens=0, is_cold_start=False, power_source="ac",
        peak_rss_mb=None, sys_used_mb=None, mem_pressure_max="", throttled=False, ok=True,
        error="", seed=42, t_start=0.0, t_end=0.0,
    )
    assert r.reasoning_text == ""
    assert r.as_dict()["reasoning_text"] == ""
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_results.py -k reasoning_text -v` → FAIL.

- [ ] **Step 3: Implement** — `ramcheck/results.py`, in `EvalResponse` direkt nach `reasoning_chars: int = 0`:

```python
    reasoning_text: str = ""  # the "thinking" text — persisted ONLY when content_empty (else "")
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_results.py -k reasoning_text -v` → PASS. `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/results.py tests/test_results.py
git commit -m "feat(results): EvalResponse.reasoning_text (persisted on content_empty)"
```

---

### Task 4: `run_eval` — effektives Budget + `extra_body` + `reasoning_text`-Persistenz

**Files:**
- Modify: `ramcheck/qualrun.py:127-135` (`stream_once`-Call), `ramcheck/qualrun.py:151-170` (`EvalResponse`-Bau)
- Test: `tests/test_qualrun.py`

- [ ] **Step 1: Failing test** — in `tests/test_qualrun.py` ergänzen. FakeClient, der `max_tokens`/`extra_body` fängt und reasoning emittiert:

```python
class CapturingClient:
    engine = "fake"
    engine_version = "0"

    def __init__(self):
        self.seen = []

    def stream(self, *, messages, model, max_tokens, temperature, seed, extra_body=None):
        self.seen.append({"max_tokens": max_tokens, "extra_body": extra_body})
        # reasoning-only: no delta_text, but reasoning present
        from ramcheck.runner import StreamEvent
        yield StreamEvent(reasoning_text="denke nach…")
        yield StreamEvent(prompt_tokens=10, completion_tokens=5)


def _config_with(model_dict):
    return Config.model_validate(
        {
            "endpoint": {"base_url": "http://localhost:11434/v1"},
            "machine": "M-test",
            "models": [model_dict],
        }
    )


def test_run_eval_adds_reasoning_headroom_to_budget(tmp_path):
    client = CapturingClient()
    cfg = _config_with({"id": "m1", "reasoning_headroom_tokens": 1000,
                        "extra_body": {"enable_thinking": False}})
    run_eval(cfg, _pack(), client, run_dir=tmp_path, sampler=NoopSampler())
    # pack prompts default max_tokens=400; effective = 400 + 1000
    assert all(s["max_tokens"] == 1400 for s in client.seen)
    assert all(s["extra_body"] == {"enable_thinking": False} for s in client.seen)


def test_run_eval_persists_reasoning_text_only_when_empty(tmp_path):
    client = CapturingClient()  # reasoning-only → content_empty
    responses = run_eval(_config_with({"id": "m1"}), _pack(), client,
                         run_dir=tmp_path, sampler=NoopSampler())
    assert all(r.content_empty for r in responses)
    assert all(r.reasoning_text == "denke nach…" for r in responses)
    # and a content answer must NOT carry reasoning_text
    responses2 = run_eval(_config_with({"id": "m2"}), _pack(), FakeClient(text="hi"),
                          run_dir=tmp_path / "b", sampler=NoopSampler())
    assert all(r.reasoning_text == "" for r in responses2)
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_qualrun.py -k "headroom or reasoning_text" -v` → FAIL (budget 400, reasoning_text leer).

- [ ] **Step 3: Implement** — `ramcheck/qualrun.py`, `stream_once`-Call (Zeile 127-135):

```python
                outcome = stream_once(
                    client,
                    messages=_messages(cell.variant, cell.prompt),
                    model=cell.model.id,
                    max_tokens=cell.prompt.max_tokens + cell.model.reasoning_headroom_tokens,
                    temperature=pack.sampling.temperature,
                    seed=pack.sampling.seed,
                    counter=counter_for(cell.model.id),
                    extra_body=cell.model.extra_body or None,
                )
```

Im `EvalResponse(...)`-Bau (nach `content_empty=...` Zeile 152): `content_empty` bleibt; und Zeile 170 (`reasoning_chars=...`) um die bedingte Persistenz ergänzen. Berechne `content_empty` einmal in eine lokale Variable, um sie zweimal zu nutzen:

```python
                is_cold = not cold_seen
                cold_seen = True
                content_empty = outcome.ok and not outcome.text.strip()
                resp = EvalResponse(
                    ...
                    response_text=outcome.text,
                    content_empty=content_empty,
                    ...
                    reasoning_chars=len(outcome.reasoning_text),
                    reasoning_text=outcome.reasoning_text if content_empty else "",
                )
```

(Den bestehenden Inline-Ausdruck `content_empty=outcome.ok and not outcome.text.strip()` durch `content_empty=content_empty` ersetzen.)

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_qualrun.py -v` (alle, inkl. der bestehenden) → PASS. `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/qualrun.py tests/test_qualrun.py
git commit -m "feat(qualrun): effective budget (prompt+headroom), forward extra_body, persist reasoning_text on empty"
```

---

## Phase B — Judge-Bewertung

### Task 5: reasoning-only → `unscored` statt stiller 1/5

**Files:**
- Modify: `ramcheck/judge.py:30-33` (`EMPTY_CONTENT_RATIONALE` splitten), `ramcheck/judge.py:192-200` (`score_response`)
- Test: `tests/test_judge.py`

- [ ] **Step 1: Failing test** — in `tests/test_judge.py` ergänzen. Der `_resp`-Helper (Zeile 56) braucht `reasoning_chars`; ergänze ihn um einen Default und nutze ihn:

```python
def test_score_response_reasoning_only_is_unscored(_pack):
    from ramcheck.judge import score_response
    prompt = _pack.all_prompts()[0][1]

    class _NoBackend:
        def judge(self, *, system, user):
            raise AssertionError("judge must not be called for empty content")

    r = _resp("A1", "A", text="", content_empty=True)
    r.reasoning_chars = 1423  # reasoning-only
    v = score_response(_NoBackend(), r, prompt, _pack)
    assert v.unscored is True
    assert v.red_flag is False
    assert "reasoning" in v.rationale.lower()


def test_score_response_truly_empty_still_scores_one(_pack):
    from ramcheck.judge import score_response
    prompt = _pack.all_prompts()[0][1]

    class _NoBackend:
        def judge(self, *, system, user):
            raise AssertionError("no judge call")

    r = _resp("A1", "A", text="", content_empty=True)
    r.reasoning_chars = 0  # genuinely empty, no thinking
    v = score_response(_NoBackend(), r, prompt, _pack)
    assert v.unscored is False
    assert v.score == 1
```

(Falls `_resp` `reasoning_chars` noch nicht setzt: in `tests/test_judge.py` den `_resp`-Helper ergänzen — der `EvalResponse(...)` braucht ohnehin schon alle Felder; `reasoning_chars`/`reasoning_text` haben Defaults, also genügt das nachträgliche `r.reasoning_chars = …` im Test.)

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_judge.py -k "reasoning_only or truly_empty" -v` → FAIL (heute score=1, unscored=False für beide).

- [ ] **Step 3: Implement** — `ramcheck/judge.py`. `EMPTY_CONTENT_RATIONALE` (Zeile 30-33) ersetzen durch zwei Konstanten:

```python
REASONING_ONLY_RATIONALE = (
    "Nur Reasoning, kein sichtbarer Content ({n} reasoning-Zeichen) — Budget zu klein oder "
    "Thinking aktiv. Nicht als sichtbare Assistenz-Antwort bewertbar (aus dem Mittel ausgenommen)."
)
EMPTY_RATIONALE = (
    "Leere Modell-Ausgabe (weder Content noch Reasoning). Als Assistenz-Antwort unbrauchbar."
)
```

`score_response` (Zeile 192-200), den `content_empty`-Zweig ersetzen:

```python
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
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_judge.py -v` → PASS (auch bestehende). Falls ein bestehender Test gegen `EMPTY_CONTENT_RATIONALE` oder das alte 1/5-Verhalten bei reasoning prüft: anpassen (reasoning-only erwartet jetzt `unscored`). `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/judge.py tests/test_judge.py
git commit -m "feat(judge): reasoning-only -> unscored (not silent 1/5); truly-empty stays 1"
```

---

### Task 6: Scorecard zeigt die reasoning-only-Quote

**Files:**
- Modify: `ramcheck/scorecard.py` (Tech-Specs-Tabelle in `render_scorecard_md`, ~144-162)
- Test: `tests/test_scorecard.py`

- [ ] **Step 1: Failing test** — in `tests/test_scorecard.py` ergänzen. Eine Hilfsfunktion zählt reasoning-only pro Gruppe; sie soll im Markdown erscheinen:

```python
def test_scorecard_surfaces_reasoning_only_count():
    from ramcheck.scorecard import reasoning_only_counts

    # build two responses, one reasoning-only
    def _r(pid, empty, rchars):
        from ramcheck.results import EvalResponse
        return EvalResponse(
            pack_id="demo", pack_version=1, machine="M", model="m", quant="", engine="e",
            engine_version="0", variant="none", category="A", prompt_id=pid, repeat=0,
            response_text="" if empty else "x", content_empty=empty, ttft_s=0.0, decode_tps=0.0,
            prefill_tps=0.0, e2e_s=0.0, prompt_tokens=0, completion_tokens=0, is_cold_start=False,
            power_source="ac", peak_rss_mb=None, sys_used_mb=None, mem_pressure_max="",
            throttled=False, ok=True, error="", seed=42, t_start=0.0, t_end=0.0, reasoning_chars=rchars,
        )
    counts = reasoning_only_counts([_r("A1", True, 1200), _r("A2", False, 0)])
    assert counts[("m", "none")] == 1
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_scorecard.py -k reasoning_only -v` → FAIL (`reasoning_only_counts` undefined).

- [ ] **Step 3: Implement** — `ramcheck/scorecard.py`, pure Helper neben `mean_score` (nach `red_flagged_prompts`, ~Zeile 63):

```python
def reasoning_only_counts(responses: list[EvalResponse]) -> dict[tuple[str, str], int]:
    """Per-(model, variant): how many answers were reasoning-only (content_empty + reasoning)."""
    out: dict[tuple[str, str], int] = {}
    for r in responses:
        if r.content_empty and r.reasoning_chars > 0:
            key = (r.model, r.variant)
            out[key] = out.get(key, 0) + 1
    return out
```

In `render_scorecard_md`, die Tech-Specs-Tabelle (Zeile 144-162) um eine Spalte „reasoning-only" erweitern. Header (Zeile 146-149):

```python
    lines.append(
        "| Modell | Variante | TTFT P50/P95 (s) | Decode (tok/s) | Peak-RAM (System) | "
        "reasoning-only | Akku? |"
    )
    lines.append("|---|---|---|---|---|:-:|---|")
```

Vor der Schleife `ro = reasoning_only_counts(responses)` setzen; in der Zeilen-Append (Zeile 155-161) die neue Spalte einfügen:

```python
    ro = reasoning_only_counts(responses)
    for model, variant in groups:
        g = [r for r in responses if r.model == model and r.variant == variant]
        p = _perf_summary(g)
        ram = p["peak_ram_gb"]
        ram_s = f"{_f(ram if isinstance(ram, float) else None)} GB" if ram is not None else "—"
        n_ro = ro.get((model, variant), 0)
        ro_s = f"⚠️ {n_ro}/{len(g)}" if n_ro else "—"
        lines.append(
            f"| {model} | {variant} | "
            f"{_f(p['ttft_p50'] if isinstance(p['ttft_p50'], float) else None, 2)} / "
            f"{_f(p['ttft_p95'] if isinstance(p['ttft_p95'], float) else None, 2)} | "
            f"{_f(p['decode_med'] if isinstance(p['decode_med'], float) else None)} | "
            f"{ram_s} | {ro_s} | {'⚠️ ja' if p['battery'] else 'nein'} |"
        )
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_scorecard.py -v` → PASS (auch bestehende Render-Tests; falls einer die Spaltenzahl der Tech-Specs-Tabelle hart prüft, anpassen). `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/scorecard.py tests/test_scorecard.py
git commit -m "feat(scorecard): surface reasoning-only count in tech-specs table"
```

---

## Phase C — Pre-Flight-Smoke

### Task 7: `preflight_models` (pure, DI-testbar)

**Files:**
- Create: `ramcheck/preflight.py`
- Test: `tests/test_preflight.py`

- [ ] **Step 1: Failing test** — `tests/test_preflight.py` neu:

```python
from ramcheck.config import ModelSpec
from ramcheck.preflight import PreflightResult, preflight_models
from ramcheck.runner import StreamEvent


class _FakeClient:
    engine = "fake"
    engine_version = "0"

    def __init__(self, mode):
        self.mode = mode  # "content" | "reasoning" | "empty" | "boom"

    def stream(self, *, messages, model, max_tokens, temperature, seed, extra_body=None):
        if self.mode == "boom":
            raise RuntimeError("model not found")
        if self.mode == "content":
            yield StreamEvent(delta_text="4")
        if self.mode == "reasoning":
            yield StreamEvent(reasoning_text="denke…")
        yield StreamEvent(prompt_tokens=3, completion_tokens=1)


def _budget(_m):
    return 64


def test_preflight_classifies_content():
    [r] = preflight_models(_FakeClient("content"), [ModelSpec(id="m")], budget_for=_budget)
    assert (r.status, r.model) == ("ok", "m")


def test_preflight_classifies_reasoning_only():
    [r] = preflight_models(_FakeClient("reasoning"), [ModelSpec(id="m")], budget_for=_budget)
    assert r.status == "reasoning_only"
    assert r.reasoning_chars > 0


def test_preflight_classifies_empty():
    [r] = preflight_models(_FakeClient("empty"), [ModelSpec(id="m")], budget_for=_budget)
    assert r.status == "empty"


def test_preflight_never_raises_on_error():
    [r] = preflight_models(_FakeClient("boom"), [ModelSpec(id="m")], budget_for=_budget)
    assert r.status == "error"
    assert "not found" in r.detail
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_preflight.py -v` → FAIL (no module `ramcheck.preflight`).

- [ ] **Step 3: Implement** — `ramcheck/preflight.py`:

```python
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
    budget_for: Callable[[ModelSpec], int],
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
                PreflightResult(m.id, "reasoning_only", tc, rc,
                                f"nur Reasoning ({rc} Zeichen), kein sichtbarer Content")
            )
        else:
            out.append(PreflightResult(m.id, "empty", tc, rc, "leere Ausgabe"))
    return out
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_preflight.py -v` → PASS. `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): preflight_models — per-model smoke classification (pure, never raises)"
```

---

### Task 8: `PREFLIGHT`-Event + `build_view`-Fold

**Files:**
- Modify: `ramcheck/events.py` (Konstante, Event-Builder, `RunView`, `build_view`, `INDEX_HTML`)
- Test: `tests/test_events.py`

- [ ] **Step 1: Failing test** — in `tests/test_events.py` ergänzen:

```python
def test_build_view_folds_preflight():
    from ramcheck.events import build_view, preflight_event

    ev = preflight_event(1.0, [
        {"model": "gemma", "status": "reasoning_only", "text_chars": 0,
         "reasoning_chars": 1400, "detail": "nur Reasoning"},
        {"model": "qwen", "status": "ok", "text_chars": 12, "reasoning_chars": 0, "detail": ""},
    ])
    view = build_view([ev])
    assert view.as_dict()["preflight"] == ev["results"]
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_events.py -k preflight -v` → FAIL.

- [ ] **Step 3: Implement** — `ramcheck/events.py`:

Konstante neben den anderen (Zeile 16-19): `PREFLIGHT = "preflight"`.

Event-Builder (neben `run_start_event`):

```python
def preflight_event(ts: float, results: list[dict[str, object]]) -> dict[str, object]:
    return {"ts": ts, "type": PREFLIGHT, "results": results}
```

`RunView` (Zeile 183-204) um ein Feld + `as_dict`-Key erweitern:

```python
    preflight: list[dict[str, object]] = field(default_factory=list)
```

und in `RunView.as_dict` `"preflight": self.preflight,` ergänzen.

`build_view` (Zeile 223-284): vor der Schleife `preflight: list[dict[str, object]] = []`; in der Schleife einen Zweig ergänzen:

```python
        elif t == PREFLIGHT:
            r = e.get("results")
            if isinstance(r, list):
                preflight = r
```

und im finalen `return RunView(...)` `preflight=preflight,` ergänzen.

`INDEX_HTML` (webmon eval-Monitor): im `es.addEventListener('view',…)`-Handler ein Warnbanner rendern. Nach `barfill.style.width=…;` einfügen:

```javascript
 const pf=(v.preflight||[]).filter(p=>p.status!=='ok');
 let pfEl=document.getElementById('pf');
 if(pf.length){pfEl.innerHTML='⚠ Pre-Flight: '+pf.map(p=>p.model+' ('+p.status+')').join(', ');pfEl.style.display='block';}
 else if(pfEl){pfEl.style.display='none';}
```

und im HTML-Body direkt nach `<h1>…</h1>` ein Element ergänzen:

```html
<div id="pf" style="display:none;background:#5a3a1a;color:#fc9;padding:.4rem .7rem;border-radius:6px;margin:.4rem 0"></div>
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_events.py -v` → PASS. `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/events.py tests/test_events.py
git commit -m "feat(events): preflight event + build_view fold + monitor warning banner"
```

---

### Task 9: `run_eval` — Pre-Flight-Integration

**Files:**
- Modify: `ramcheck/qualrun.py:70-82` (Signatur), `ramcheck/qualrun.py:111-115` (vor `sampler.start()`)
- Test: `tests/test_qualrun.py`

- [ ] **Step 1: Failing test** — in `tests/test_qualrun.py` ergänzen:

```python
def test_run_eval_runs_preflight_and_calls_callback(tmp_path):
    seen = {}

    def on_pf(results):
        seen["results"] = results

    run_eval(_config_with({"id": "m1"}), _pack(), FakeClient(text="hi"),
             run_dir=tmp_path, sampler=NoopSampler(), on_preflight=on_pf)
    assert "results" in seen
    assert seen["results"][0].status == "ok"


def test_run_eval_strict_preflight_aborts_on_reasoning_only(tmp_path):
    client = CapturingClient()  # reasoning-only
    with pytest.raises(RuntimeError, match="Pre-Flight"):
        run_eval(_config_with({"id": "m1"}), _pack(), client,
                 run_dir=tmp_path, sampler=NoopSampler(), strict_preflight=True)


def test_run_eval_resume_skips_preflight(tmp_path):
    # seed a done bundle first
    run_eval(_config_with({"id": "m1"}), _pack(), FakeClient(text="hi"),
             run_dir=tmp_path, sampler=NoopSampler())
    called = {"n": 0}
    run_eval(_config_with({"id": "m1"}), _pack(), FakeClient(text="hi"),
             run_dir=tmp_path, sampler=NoopSampler(), resume=True,
             on_preflight=lambda r: called.__setitem__("n", called["n"] + 1))
    assert called["n"] == 0
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_qualrun.py -k preflight -v` → FAIL (`run_eval` has no `on_preflight`).

- [ ] **Step 3: Implement** — `ramcheck/qualrun.py`. Signatur (Zeile 79-81) um zwei Parameter ergänzen:

```python
    on_run_start: Callable[[int], None] | None = None,
    on_cell_start: Callable[[int, EvalCell], None] | None = None,
    on_cell_done: Callable[[int, EvalResponse], None] | None = None,
    on_preflight: Callable[[list["PreflightResult"]], None] | None = None,
    strict_preflight: bool = False,
```

Import oben ergänzen: `from ramcheck.preflight import PreflightResult, preflight_models`.

Pre-Flight-Block nach `cells = iter_eval_cells(config, pack)` (Zeile 111), VOR `if on_run_start…` einfügen:

```python
    cells = iter_eval_cells(config, pack)
    if not resume:
        max_prompt_budget = max(
            (p.max_tokens for _, p in pack.all_prompts()), default=0
        )

        def _budget_for(m: ModelSpec) -> int:
            return max_prompt_budget + m.reasoning_headroom_tokens

        pf = preflight_models(client, config.models, budget_for=_budget_for)
        if on_preflight is not None:
            on_preflight(pf)
        bad = [r for r in pf if r.status != "ok"]
        if strict_preflight and bad:
            raise RuntimeError(
                "Pre-Flight: "
                + "; ".join(f"{r.model} → {r.status} ({r.detail})" for r in bad)
            )
    if on_run_start is not None:
        on_run_start(len(cells))
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_qualrun.py -v` → PASS. `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/qualrun.py tests/test_qualrun.py
git commit -m "feat(qualrun): run preflight before matrix (callback + strict abort; resume skips)"
```

---

### Task 10: CLI `--strict-preflight` + Surfacing (Print + Event)

**Files:**
- Modify: `ramcheck/cli.py:244-310` (`_eval_event_writers` → 5. Closure `on_preflight`), `ramcheck/cli.py:492-567` (`eval_cmd`)
- Test: `tests/test_cli_eval.py` (oder die vorhandene CLI-Test-Datei)

- [ ] **Step 1: Failing test** — die `_eval_event_writers` gibt künftig 5 statt 4 Closures zurück; teste, dass ein `preflight`-Event geschrieben wird. In der CLI-Test-Datei ergänzen:

```python
def test_eval_event_writers_emits_preflight(tmp_path):
    from ramcheck.cli import _eval_event_writers
    from ramcheck.preflight import PreflightResult
    import json

    ep = tmp_path / "events.jsonl"
    on_run_start, on_cell_start, on_cell_done, on_preflight, run_done = _eval_event_writers(
        ep, append=False
    )
    on_preflight([PreflightResult("gemma", "reasoning_only", 0, 1400, "nur Reasoning")])
    run_done([])
    types = [json.loads(l)["type"] for l in ep.read_text().splitlines()]
    assert "preflight" in types
```

- [ ] **Step 2: Run, expect FAIL** — FAIL (`_eval_event_writers` returns 4 values).

- [ ] **Step 3: Implement** — `ramcheck/cli.py`, `_eval_event_writers`:

Rückgabetyp-Tuple (Zeile 248-253) um eine Closure erweitern (zwischen `on_cell_done` und `run_done`):

```python
    Callable[[list[PreflightResult]], None],
```

(Import oben in cli.py ergänzen: `from ramcheck.preflight import PreflightResult`.)

Closure definieren (vor `def run_done`):

```python
    def on_preflight(results: list[PreflightResult]) -> None:
        _w(events_mod.preflight_event(time.time(), [r.as_dict() for r in results]))
```

und den `return` (Zeile 310) ändern zu:

```python
    return on_run_start, on_cell_start, on_cell_done, on_preflight, run_done
```

`eval_cmd` (cli.py): neues Flag nach `models_json` (Zeile 513) ergänzen:

```python
    strict_preflight: bool = typer.Option(
        False, "--strict-preflight", help="abort before the matrix if a model emits no visible content"
    ),
```

Eine kleine Print-Closure definieren (nach `client = _make_client(cfg)`, Zeile 537), die in BEIDEN Pfaden genutzt wird:

```python
    def _print_preflight(results: list[PreflightResult]) -> None:
        bad = [r for r in results if r.status != "ok"]
        for r in bad:
            console.print(f"[yellow]⚠ Pre-Flight[/] {r.model}: {r.status} — {r.detail}")
        if not bad:
            console.print("[green]✓ Pre-Flight[/] alle Modelle liefern sichtbaren Content")
```

(Import `from ramcheck.preflight import PreflightResult` oben in cli.py — schon in Step 3.)

Non-emit-Pfad (Zeile 539-542):

```python
    if not emit:
        responses = run_eval(
            cfg, pk, client, run_dir=run_dir, resume=resume is not None,
            on_preflight=_print_preflight, strict_preflight=strict_preflight,
        )
        _finalize_eval_bundle(run_dir, pack, cfg, pk, responses)
        return
```

Emit-Pfad: die Writer-Entpackung (Zeile 550-552) auf 5 Werte erweitern und ein kombiniertes `on_preflight` bauen (Print + Event):

```python
        on_run_start, on_cell_start, on_cell_done, on_preflight_evt, run_done = _eval_event_writers(
            run_dir / "events.jsonl", append=web
        )

        def _on_preflight(results: list[PreflightResult]) -> None:
            _print_preflight(results)
            on_preflight_evt(results)
```

und der `run_eval(...)`-Aufruf (Zeile 555-564) bekommt `on_preflight=_on_preflight, strict_preflight=strict_preflight,`.

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/ -k "preflight" -v` → PASS. `uv run mypy ramcheck/`. (Beachte: `_run_event_writers` für den Chat-`run` bleibt unverändert — der Pre-Flight ist eval-only.)

- [ ] **Step 5: Commit**

```bash
git add ramcheck/cli.py tests/
git commit -m "feat(cli): eval --strict-preflight + preflight surfacing (console + events.jsonl)"
```

---

### Task 11: GUI-Live-Karte zeigt den Pre-Flight-Befund

**Files:**
- Read first: `ramcheck/gui/live.py` (fold/SSE-Pfad der GUI), `ramcheck/gui/templates/*` (Live-Karte), zugehöriges JS
- Modify: das GUI-Live-Template + sein EventSource-JS-Handler (analog zur `INDEX_HTML`-Änderung aus Task 8)
- Verify: headless Chrome SSE-Smoke

- [ ] **Step 1: Read the GUI live path** — `ramcheck/gui/live.py` lesen: ob es `events.build_view` wiederverwendet (dann trägt das `view`-JSON bereits `preflight`) oder ein eigenes Fold hat. Falls eigenes Fold: den `preflight`-Schlüssel analog zu Task 8 durchreichen.

- [ ] **Step 2: Implement the banner** — im GUI-Live-Template (die Progress-Karte, die in der letzten Session auf natives `EventSource` umgestellt wurde) ein verstecktes Warn-`<div id="pf">` ergänzen und im `view`-Event-Handler füllen — exakt das Muster aus Task 8 (`v.preflight.filter(p=>p.status!=='ok')`).

- [ ] **Step 3: Restart + headless verify** — Server neu starten (Routen-Code lädt sonst nicht):

```bash
pkill -f "ramcheck gui" 2>/dev/null; sleep 1
uv run ramcheck gui --no-open --port 8765 &
sleep 2
```

Dann einen Eval-Lauf mit einem reasoning-only-Fake/echten Modell starten und prüfen, dass die `view`-SSE-Payload `preflight` mit `status:"reasoning_only"` trägt:

```bash
curl -sN "http://127.0.0.1:8765/live/<run_dir_name>?kind=eval" | head -c 2000
```

Erwartung: im `event: view`-`data:` taucht `"preflight":[{...,"status":"reasoning_only",...}]` auf. (Markup der Karte zusätzlich mit `--dump-dom` auf das `#pf`-Element prüfen, BEVOR die SSE geöffnet wird — eine offene EventSource verhindert den DOM-Snapshot.)

- [ ] **Step 4: Commit**

```bash
git add ramcheck/gui/
git commit -m "feat(gui): live preflight warning banner in the progress card"
```

---

## Phase D — Judge-Modell-Picker

### Task 12: Discovery generisch + Judge-Variante

**Files:**
- Modify: `ramcheck/gui/configs.py` (Kern-Helper `discover_models` + `discover_judge_endpoint_models`)
- Test: `tests/test_gui_configs.py`

- [ ] **Step 1: Failing test** — in `tests/test_gui_configs.py` ergänzen:

```python
def test_discover_models_generic_dedupes_and_never_raises():
    from ramcheck.gui.configs import discover_models

    out = discover_models("http://x/v1", "k", lister=lambda: ["a", "a", "b"])
    assert out == {"models": ["a", "b"], "error": None}

    boom = discover_models("http://x/v1", "k", lister=lambda: (_ for _ in ()).throw(RuntimeError("dead")))
    assert boom["models"] == [] and "dead" in boom["error"]


def test_discover_judge_endpoint_models(tmp_path):
    from ramcheck.gui.configs import discover_judge_endpoint_models

    jc = tmp_path / "judge.yaml"
    jc.write_text("endpoint:\n  base_url: http://x/v1\nmodel: qwen\n", encoding="utf-8")
    out = discover_judge_endpoint_models(str(jc), lister=lambda: ["qwen", "gemma"])
    assert out == {"models": ["qwen", "gemma"], "error": None}
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_gui_configs.py -k "discover_models or judge_endpoint" -v` → FAIL.

- [ ] **Step 3: Implement** — `ramcheck/gui/configs.py`. Kern-Helper extrahieren und `discover_endpoint_models` darauf umstellen:

```python
def discover_models(
    base_url: str,
    api_key: str,
    *,
    lister: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    """{"models": [...], "error": str|None}. NEVER raises. Shared by eval + judge discovery."""
    if lister is None:

        def lister() -> list[str]:
            from ramcheck.client import OpenAIStreamClient

            client = OpenAIStreamClient(base_url, api_key, timeout=3.0, max_retries=0)
            return client.list_models()

    try:
        seen: set[str] = set()
        out: list[str] = []
        for m in lister():
            if m not in seen:
                seen.add(m)
                out.append(m)
    except Exception as e:
        return {"models": [], "error": f"Endpoint nicht erreichbar: {e}"}
    return {"models": out, "error": None}


def discover_endpoint_models(
    config_path: str | Path,
    *,
    lister: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    """Eval-config variant: load the config, discover its endpoint's models."""
    if lister is None:
        try:
            cfg = load_config(config_path)
        except Exception as e:
            return {"models": [], "error": f"Config nicht lesbar: {e}"}
        return discover_models(cfg.endpoint.base_url, cfg.endpoint.api_key)
    return discover_models("", "", lister=lister)


def discover_judge_endpoint_models(
    judge_config_path: str | Path,
    *,
    lister: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    """Judge-config variant: load the JudgeConfig, discover its endpoint's models."""
    if lister is None:
        from ramcheck.judge import load_judge_config

        try:
            jc = load_judge_config(judge_config_path)
        except Exception as e:
            return {"models": [], "error": f"Judge-Config nicht lesbar: {e}"}
        return discover_models(jc.endpoint.base_url, jc.endpoint.api_key)
    return discover_models("", "", lister=lister)
```

(Bestehende `discover_endpoint_models`-Aufrufer bleiben kompatibel — gleiche Signatur, gleicher Rückgabe-Kontrakt.)

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_gui_configs.py -v` → PASS (auch der bestehende `discover_endpoint_models`-Test). `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/configs.py tests/test_gui_configs.py
git commit -m "refactor(gui): generic discover_models + discover_judge_endpoint_models"
```

---

### Task 13: Route `GET /judge-endpoint-models`

**Files:**
- Modify: `ramcheck/gui/app.py:168-177` (neben `/endpoint-models`)
- Test: `tests/test_gui_app.py` (FastAPI TestClient)

- [ ] **Step 1: Failing test** — in `tests/test_gui_app.py` ergänzen (Muster des bestehenden `/endpoint-models`-Tests spiegeln):

```python
def test_judge_endpoint_models_guards_path_and_never_500(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from ramcheck.gui.app import create_app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "judge.yaml").write_text(
        "endpoint:\n  base_url: http://x/v1\nmodel: qwen\n", encoding="utf-8"
    )
    (tmp_path / "runs").mkdir()
    client = TestClient(create_app(runs_dir=tmp_path / "runs"))

    # unknown / traversal path → 404
    assert client.get("/judge-endpoint-models?judge_config=/etc/passwd").status_code == 404
    # known judge config → 200, never 500 even if endpoint is dead
    r = client.get("/judge-endpoint-models?judge_config=judge.yaml")
    assert r.status_code == 200
    assert "models" in r.json() and "error" in r.json()
```

(Falls die App-Factory anders heißt als `create_app` — den Namen aus `tests/test_gui_app.py` übernehmen.)

- [ ] **Step 2: Run, expect FAIL** — FAIL (404 für die noch fehlende Route bzw. Route nicht registriert).

- [ ] **Step 3: Implement** — `ramcheck/gui/app.py`, direkt nach der `/endpoint-models`-Route (Zeile 177):

```python
    @app.get("/judge-endpoint-models")
    def judge_endpoint_models(judge_config: str) -> dict[str, Any]:
        """Models the selected judge config's endpoint advertises. Never 500s. Same path guard
        as /endpoint-models: only the judge*.yaml files the picker actually offers."""
        if judge_config not in {str(p) for p in Path(".").glob("judge*.yaml")}:
            raise HTTPException(status_code=404)
        return configs_mod.discover_judge_endpoint_models(judge_config)
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_gui_app.py -k judge_endpoint -v` → PASS. `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/app.py tests/test_gui_app.py
git commit -m "feat(gui): /judge-endpoint-models route (never-500, path-guarded)"
```

---

### Task 14: CLI `judge --judge-model` override

**Files:**
- Modify: `ramcheck/cli.py:621-647` (`judge` command)
- Test: CLI-Test-Datei

- [ ] **Step 1: Failing test** — die Override-Logik ist pur testbar über `JudgeConfig.model_copy`. Test in der CLI-Test-Datei:

```python
def test_judge_model_override_replaces_config_model():
    from ramcheck.judge import JudgeConfig, JudgeEndpoint

    jc = JudgeConfig(endpoint=JudgeEndpoint(base_url="http://x/v1"), model="qwen", temperature=0.0)
    overridden = jc.model_copy(update={"model": "gemma"}) if "gemma" else jc
    assert overridden.model == "gemma"
    assert overridden.endpoint.base_url == "http://x/v1"  # endpoint untouched
```

(Dies pinnt das Override-Muster fest; der CLI-Flag-Pfad wird im Smoke der GUI/CLI mitgeprüft.)

- [ ] **Step 2: Run, expect FAIL/PASS** — Falls `JudgeEndpoint` nicht exportiert: Import anpassen. Test soll grün das Muster zeigen.

- [ ] **Step 3: Implement** — `ramcheck/cli.py`, `judge`-command. Flag nach `--judge-config` (Zeile 626) ergänzen:

```python
    judge_model: str = typer.Option(
        "", "--judge-model", help="override the judge model id from the config (GUI picker)"
    ),
```

Nach `jc = load_judge_config(judge_config)` (Zeile 644) override anwenden:

```python
    jc = load_judge_config(judge_config)
    if judge_model.strip():
        jc = jc.model_copy(update={"model": judge_model.strip()})
    backend = OpenAIJudgeBackend(
        jc.endpoint.base_url, jc.endpoint.api_key, jc.model, jc.temperature
    )
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/ -k judge_model -v` → PASS. `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/cli.py tests/
git commit -m "feat(cli): judge --judge-model overrides the config model (no write-back)"
```

---

### Task 15: `start_judge` argv + `POST /runs/judge` `judge_model`

**Files:**
- Modify: `ramcheck/gui/control.py:220-236` (`start_judge`), `ramcheck/gui/app.py:253-260` (`POST /runs/judge`)
- Test: `tests/test_gui_control.py`, `tests/test_gui_app.py`

- [ ] **Step 1: Failing test (control)** — in `tests/test_gui_control.py` ergänzen (Spy-Launcher-Muster der bestehenden Tests nutzen):

```python
def test_start_judge_appends_judge_model_to_argv(tmp_path):
    # use the existing fake launcher / registry fixture pattern from this file
    reg, launcher = _make_registry(tmp_path)  # adapt to the file's existing helper
    bundle = tmp_path / "runs" / "b"; bundle.mkdir(parents=True)
    reg.start_judge(bundle=bundle, judge_config_path="judge.yaml", judge_model="gemma")
    assert "--judge-model" in launcher.last_argv
    assert "gemma" in launcher.last_argv
```

- [ ] **Step 2: Run, expect FAIL** — FAIL (`start_judge` has no `judge_model`).

- [ ] **Step 3: Implement** — `ramcheck/gui/control.py`, `start_judge`:

```python
    def start_judge(
        self, *, bundle: Path, judge_config_path: str, judge_model: str = ""
    ) -> RunHandle:
        with self._lock:
            self._guard_free()
            argv = [
                "judge",
                "--bundle",
                str(bundle),
                "--judge-config",
                judge_config_path,
                "--emit-events",
            ]
            if judge_model.strip():
                argv += ["--judge-model", judge_model.strip()]
            write_sentinel(
                bundle, kind="judge", pid=-1, pack_path="", config_path=judge_config_path
            )
            pid = self.launcher.spawn(argv)
            set_sentinel_pid(bundle, pid)
            return RunHandle("judge", bundle, pid)
```

`ramcheck/gui/app.py`, `POST /runs/judge` (Zeile 253-260):

```python
    @app.post("/runs/judge")
    def start_judge(
        bundle: str = Form(...),
        judge_config_path: str = Form(...),
        judge_model: str = Form(""),
    ) -> Any:
        bundle_dir = _confine(bundle)
        try:
            h = registry.start_judge(
                bundle=bundle_dir, judge_config_path=judge_config_path, judge_model=judge_model
            )
        except RunInProgress as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"run_dir": h.run_dir.name, "kind": h.kind}
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_gui_control.py tests/test_gui_app.py -k judge -v` → PASS. `uv run mypy ramcheck/`.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/control.py ramcheck/gui/app.py tests/
git commit -m "feat(gui): thread judge_model through /runs/judge -> start_judge -> --judge-model"
```

---

### Task 16: Judge-Form Modell-Dropdown + `judgeModelPicker` JS

**Files:**
- Modify: `ramcheck/gui/app.py:153-166` (`config_get` Kontext: `judge_models_by_config`), `ramcheck/gui/templates/config.html:119-127` (Judge-Form), `ramcheck/gui/static/model_picker.js` (neue Komponente)
- Verify: headless Chrome `--dump-dom`

- [ ] **Step 1: Implement template + JS** — `ramcheck/gui/static/model_picker.js`, am Ende (vor dem schließenden `});` der `alpine:init`-Registrierung) eine zweite Komponente registrieren:

```javascript
  Alpine.data("judgeModelPicker", () => ({
    judgeConfig: "",
    models: [],
    pick: "",
    loading: false,
    error: "",
    init() {
      // sync to the judge_config_path select's initial value, then fetch
      const sel = document.getElementById("judge_config_path");
      this.judgeConfig = sel ? sel.value : "";
      this.fetchModels();
    },
    async fetchModels() {
      const cfg = this.judgeConfig;
      if (!cfg) return;
      this.loading = true; this.error = ""; this.models = []; this.pick = "";
      try {
        const res = await fetch("/judge-endpoint-models?judge_config=" + encodeURIComponent(cfg));
        const data = await res.json();
        if (this.judgeConfig !== cfg) return;
        this.models = data.models || [];
        this.error = data.error || "";
      } catch (e) {
        if (this.judgeConfig === cfg) this.error = "Endpoint-Abfrage fehlgeschlagen";
      } finally {
        if (this.judgeConfig === cfg) this.loading = false;
      }
    },
  }));
```

`ramcheck/gui/templates/config.html`, die Judge-Config-`<select>` (Zeile 121) um `@change` ergänzen und einen Picker-Block einhängen. Das umgebende `<form>` (Zeile 107) bekommt `x-data="judgeModelPicker()"`. Die Judge-Config-Select:

```html
      <select class="form-select" id="judge_config_path" name="judge_config_path"
              x-model="judgeConfig" @change="fetchModels()">
        {% for j in judge_configs %}
        <option value="{{ j }}">{{ j }}</option>
        {% endfor %}
      </select>
```

Nach dieser `form-group` einen Modell-Picker einfügen:

```html
    <div class="form-group">
      <label class="form-label" for="judge_model">Judge-Modell (vom Endpoint)</label>
      <select class="form-select" name="judge_model" x-model="pick"
              :disabled="models.length === 0">
        <option value="">— Config-Default verwenden —</option>
        <template x-for="mid in models" :key="mid">
          <option :value="mid" x-text="mid"></option>
        </template>
      </select>
      <p class="muted text-xs" x-show="loading" style="margin-top:0.2rem">… Endpoint wird abgefragt</p>
      <p class="muted text-xs" x-show="error" x-text="error" style="color:var(--einschr); margin-top:0.2rem"></p>
    </div>
```

(Leerer `judge_model` → CLI nutzt den Config-Default; das deckt sich mit Task 14/15.)

- [ ] **Step 2: Restart + headless verify** — Server neu starten, dann die Config-Seite mit headless Chrome `--dump-dom` rendern und prüfen, dass das Judge-Modell-`<select>` existiert und der Picker bei Config-Wechsel `/judge-endpoint-models` abfragt:

```bash
pkill -f "ramcheck gui" 2>/dev/null; sleep 1
uv run ramcheck gui --no-open --port 8765 &
sleep 2
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --dump-dom \
  "http://127.0.0.1:8765/config" 2>/dev/null | grep -c 'name="judge_model"'
```

Erwartung: `1` (das Select ist im Markup). Zusätzlich den Fetch verifizieren:

```bash
curl -s "http://127.0.0.1:8765/judge-endpoint-models?judge_config=judge.yaml"
```

Erwartung: `{"models": [...], "error": ...}` (bei totem Endpoint leere Liste + error — nie 500).

- [ ] **Step 3: Commit**

```bash
git add ramcheck/gui/static/model_picker.js ramcheck/gui/templates/config.html ramcheck/gui/app.py
git commit -m "feat(gui): judge-model picker dropdown (reuses /judge-endpoint-models)"
```

---

## Phase E — Doku

### Task 17: AGENTS.md · design-decisions.md · _method_explainer.html

**Files:**
- Modify: `AGENTS.md` (Gotchas-Abschnitt)
- Modify: `docs/explanation/design-decisions.md`
- Modify: `ramcheck/gui/templates/_method_explainer.html`

- [ ] **Step 1: AGENTS.md** — im Gotchas-Block den „Reasoning models"-Eintrag aktualisieren/ergänzen:
  - reasoning-only-Antworten → `unscored` (nicht mehr stilles 1/5); echt-leer bleibt 1/5.
  - Pre-Flight-Smoke vor der Matrix; Default warnt, `--strict-preflight` bricht ab; resume überspringt.
  - `ModelSpec.reasoning_headroom_tokens` (extra Gesamt-Budget je Modell, sichtbares Budget bleibt `pack.prompt.max_tokens`) + `extra_body` (generisch durchgereicht, z. B. Thinking aus — engine-agnostisch).
  - Judge-Modell-Picker: `/judge-endpoint-models` + `judge --judge-model`.

- [ ] **Step 2: design-decisions.md** — Abschnitt „Thinking-Modelle" ergänzen: warum `unscored` statt 1/5 (es ist *unser* Budget-Setup, nicht das Modell), warum die faire Chance **opt-in** ist (ein kleines Budget ist ein legitimes Test-Setup → Mess-Bedingung nie heimlich ändern), warum der Smoke das großzügigste Budget nimmt.

- [ ] **Step 3: _method_explainer.html** — die reasoning-only/`unscored`-Behandlung im UI-abrufbaren Methoden-Text erklären (eine Zeile/Absatz: leere Antworten von Reasoning-Modellen werden als „reasoning-only" markiert und aus dem Mittel genommen, nicht als 1/5 gewertet).

- [ ] **Step 4: Verify + Commit** — `uv run pytest -q` (alles grün), `uv run ruff check . && uv run mypy ramcheck/`. Falls ein `tests/test_config.py`-Guard die geshippten `config.*.yaml` validiert: unberührt (additive Felder). 

```bash
git add AGENTS.md docs/explanation/design-decisions.md ramcheck/gui/templates/_method_explainer.html
git commit -m "docs: thinking-models handling, pre-flight, budget opt-in, judge picker"
```

---

## Abschluss

- [ ] **Voll-Suite + Lint + Typen:** `uv run pytest -q && uv run ruff check . && uv run ruff format . && uv run mypy ramcheck/` — alles grün.
- [ ] **End-to-End-Smoke (manuell, falls ein echtes Modell läuft):** gemma-4-12b-qat (LM Studio :1234) ohne Headroom → Pre-Flight warnt „reasoning_only", Scorecard zeigt reasoning-only-Quote, Verdicts `unscored` statt 1/5. Dann `reasoning_headroom_tokens: 2000` oder `extra_body` Thinking-aus → Pre-Flight `ok`, echte Antworten.
- [ ] **Merge:** Feature-Branch `feat/thinking-models-blocker` → `main` (Solo-Repo: direkt mergen + nach Codeberg pushen, kein PR).
```
