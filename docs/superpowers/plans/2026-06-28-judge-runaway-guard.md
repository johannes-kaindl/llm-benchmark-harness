# Judge-Runaway-Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein durchdrehendes/lahmer Judge-Call scheitert schnell (Timeout, keine Retries) und nicht-destruktiv (gefangen → unscored `judge_error`-Verdict + Circuit-Breaker), statt nach ~30 min den Judge-Prozess zu crashen.

**Architecture:** Zwei Schichten in `touchstone/judge.py`: (1) `OpenAIJudgeBackend` bindet den Call (timeout + max_retries=0 + optional max_tokens) und wirft `JudgeCallError`; (2) `judge_responses`/`score_dimensions` fangen das → degradierte Verdicts/Report, `judge_responses` zählt aufeinanderfolgende Fehler und wirft `JudgeAborted` ab einem Schwellwert. Config auf `JudgeConfig` (backward-kompatible Defaults); CLI/GUI erben automatisch.

**Tech Stack:** Python 3.12, uv, pydantic, OpenAI SDK, Typer, pytest. Ruff (line-length 100) + mypy strict.

## Global Constraints

- Python 3.12, `uv` only. Ruff line-length 100, mypy strict, alle Tests grün (`uv run pytest -q`).
- Code/Identifier englisch; Prosa/Begründungen/Commits dürfen deutsch.
- I/O dependency-injected → ohne Server testbar (Fake-Backend/Fake-OpenAI-Client).
- Bestehende judge-Tests bleiben grün (fehlerfreies Verhalten unverändert).
- `max_tokens` default `null` (kein Cap); Timeout ist der primäre Guard.

---

### Task 1: `Verdict.judge_error` Feld

**Files:**
- Modify: `touchstone/results.py:70-86`
- Test: `tests/test_results.py` (oder vorhandene Verdict-Test-Datei; sonst `tests/test_judge_guard.py`)

**Interfaces:**
- Produces: `Verdict(..., unscored: bool=False, safety_critical: bool=False, judge_error: bool=False)` — `as_dict()` (asdict) enthält `judge_error`; `Verdict(**json)` round-trippt (alte Zeilen ohne Feld → False).

- [ ] **Step 1: Failing test**

```python
# tests/test_judge_guard.py
from touchstone.results import Verdict

def test_verdict_judge_error_defaults_false_and_roundtrips():
    v = Verdict(model="m", variant="none", prompt_id="A1", repeat=0, category="A",
                score=0, red_flag=False, rationale="x")
    assert v.judge_error is False
    d = v.as_dict()
    assert d["judge_error"] is False
    assert Verdict(**d).judge_error is False
    assert Verdict(model="m", variant="none", prompt_id="A1", repeat=0, category="A",
                   score=0, red_flag=False, rationale="x", judge_error=True).judge_error is True
```

- [ ] **Step 2: Run → FAIL** (`TypeError: judge_error` / `KeyError`).
  Run: `uv run pytest tests/test_judge_guard.py -q`

- [ ] **Step 3: Implement** — add the field after `safety_critical` in `touchstone/results.py`:

```python
    unscored: bool = False  # judge unreachable / unparseable → excluded from means
    safety_critical: bool = False
    judge_error: bool = False  # judge CALL failed (timeout/API) → unscored + NOT persisted
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Lint/type/commit**

```bash
uv run ruff check touchstone/results.py tests/test_judge_guard.py && uv run mypy touchstone/results.py
git add touchstone/results.py tests/test_judge_guard.py
git commit -m "feat(judge): Verdict.judge_error flag (call failure, excluded + not persisted)"
```

---

### Task 2: `JudgeCallError` + bounded `OpenAIJudgeBackend`

**Files:**
- Modify: `touchstone/judge.py` (add exceptions near top; edit `OpenAIJudgeBackend` ~368-388)
- Test: `tests/test_judge_guard.py`

**Interfaces:**
- Produces:
  - `class JudgeCallError(Exception)` , `class JudgeAborted(Exception)`
  - `OpenAIJudgeBackend(base_url, api_key, model, temperature=0.0, *, timeout: float|None=None, max_retries: int=0, max_tokens: int|None=None)`
  - `.judge(*, system, user) -> str` — raises `JudgeCallError` on any SDK/connection exception; forwards `max_tokens` only when set.

- [ ] **Step 1: Failing tests** (inject a fake OpenAI client via monkeypatching the import is awkward; instead test the wrap by subclassing — set `_client` to a fake):

```python
import pytest
from touchstone import judge as J

class _FakeCompletions:
    def __init__(self, exc=None, content="ok"): self._exc, self._content = exc, content; self.kwargs=None
    def create(self, **kw):
        self.kwargs = kw
        if self._exc: raise self._exc
        class _M: 
            class choices_item:
                class message: content = None
        m = type("R", (), {"choices": [type("C", (), {"message": type("Msg", (), {"content": self._content})()})()]})
        return m

def _backend_with(fake):
    b = J.OpenAIJudgeBackend.__new__(J.OpenAIJudgeBackend)
    b._model = "jm"; b._temperature = 0.0; b._max_tokens = None
    b._client = type("Cli", (), {"chat": type("Chat", (), {"completions": fake})()})()
    return b

def test_judge_wraps_exception_as_judgecallerror():
    b = _backend_with(_FakeCompletions(exc=RuntimeError("boom")))
    with pytest.raises(J.JudgeCallError):
        b.judge(system="s", user="u")

def test_judge_returns_content_on_success():
    b = _backend_with(_FakeCompletions(content="hi"))
    assert b.judge(system="s", user="u") == "hi"

def test_judge_forwards_max_tokens_only_when_set():
    fake = _FakeCompletions(content="x"); b = _backend_with(fake); b._max_tokens = 256
    b.judge(system="s", user="u")
    assert fake.kwargs["max_tokens"] == 256
    fake2 = _FakeCompletions(content="x"); b2 = _backend_with(fake2)  # _max_tokens None
    b2.judge(system="s", user="u")
    assert "max_tokens" not in fake2.kwargs
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: JudgeCallError`).

- [ ] **Step 3: Implement** — near the top of `touchstone/judge.py` (after imports):

```python
class JudgeCallError(Exception):
    """A single judge LLM call failed (timeout / API / connection)."""


class JudgeAborted(Exception):
    """Too many judge calls failed in a row — the judge model is unusable; abort the run."""
```

Replace `OpenAIJudgeBackend` (~368-388) with:

```python
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

        kwargs: dict[str, object] = {"base_url": base_url, "api_key": api_key,
                                     "max_retries": max_retries}
        if timeout is not None:
            kwargs["timeout"] = timeout
        self._client = OpenAI(**kwargs)
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
            resp = self._client.chat.completions.create(**call)  # type: ignore[arg-type]
        except Exception as e:  # APITimeoutError / APIError / connection — fail fast, never hang
            raise JudgeCallError(f"{type(e).__name__}: {e}") from e
        return resp.choices[0].message.content or ""
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Lint/type/commit**

```bash
uv run ruff check touchstone/judge.py tests/test_judge_guard.py && uv run mypy touchstone/judge.py
git add -u && git commit -m "feat(judge): bounded OpenAIJudgeBackend (timeout/max_retries=0/max_tokens) + JudgeCallError"
```

---

### Task 3: `JudgeConfig` guard fields

**Files:**
- Modify: `touchstone/judge.py:355-358` (JudgeConfig)
- Test: `tests/test_judge_guard.py`

**Interfaces:**
- Produces: `JudgeConfig(endpoint, model, temperature=0.0, call_timeout_s: float=120, max_consecutive_failures: int=3, max_tokens: int|None=None)`

- [ ] **Step 1: Failing test**

```python
from touchstone.judge import JudgeConfig, JudgeEndpoint

def test_judge_config_guard_defaults():
    jc = JudgeConfig(endpoint=JudgeEndpoint(base_url="x"), model="m")
    assert jc.call_timeout_s == 120
    assert jc.max_consecutive_failures == 3
    assert jc.max_tokens is None
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: call_timeout_s`).

- [ ] **Step 3: Implement** — extend `JudgeConfig`:

```python
class JudgeConfig(BaseModel):
    endpoint: JudgeEndpoint
    model: str
    temperature: float = 0.0
    call_timeout_s: float = 120  # per-call timeout — fail fast instead of ~30 min
    max_consecutive_failures: int = 3  # circuit-breaker; 0 = disabled
    max_tokens: int | None = None  # optional cap; default off (no truncation risk)
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**

```bash
uv run ruff check touchstone/judge.py && uv run mypy touchstone/judge.py
git add -u && git commit -m "feat(judge): JudgeConfig guard fields (call_timeout_s/max_consecutive_failures/max_tokens)"
```

---

### Task 4: `judge_responses` degrade + circuit-breaker

**Files:**
- Modify: `touchstone/judge.py:240-262` (judge_responses) + add `JUDGE_ERROR_RATIONALE`
- Test: `tests/test_judge_guard.py`

**Interfaces:**
- Consumes: `JudgeCallError`, `JudgeAborted`, `Verdict.judge_error`, `_verdict`, `score_response`
- Produces: `judge_responses(backend, responses, pack, *, skip_keys=frozenset(), on_verdict=None, max_consecutive_failures: int = 0) -> list[Verdict]`
  - On a `JudgeCallError` from `score_response`: append an `unscored=True, judge_error=True` verdict (rationale `JUDGE_ERROR_RATIONALE`), do NOT call `on_verdict`, increment a consecutive counter; on success reset it; raise `JudgeAborted` when `max_consecutive_failures` (>0) is reached.

- [ ] **Step 1: Failing tests**

```python
from touchstone.pack import load_pack
from touchstone.results import EvalResponse
from touchstone import judge as J

class _ThrowBackend:
    def __init__(self, fail_first=999): self.calls=0; self.fail_first=fail_first
    def judge(self, *, system, user):
        self.calls += 1
        if self.calls <= self.fail_first: raise J.JudgeCallError("boom")
        return '{"score": 4, "red_flag": false, "rationale": "ok"}'

def _resps(pack, n):  # n content-bearing responses for the first n prompts
    out=[]; prompts=[p for _,p in pack.all_prompts()][:n]
    for p in prompts:
        out.append(EvalResponse(model="m", variant="none", prompt_id=p.id, repeat=0,
                                category=p.category, response_text="eine Antwort", ok=True,
                                content_empty=False, reasoning_chars=0))
    return out

def test_judge_responses_marks_call_failure_as_judge_error(buero_pack):
    resps = _resps(buero_pack, 1)
    vs = J.judge_responses(_ThrowBackend(), resps, buero_pack)  # threshold default 0 = no abort
    assert vs[0].judge_error is True and vs[0].unscored is True
    assert "Judge-Fehler" in vs[0].rationale

def test_judge_responses_circuit_breaker_aborts(buero_pack):
    resps = _resps(buero_pack, 5)
    with pytest.raises(J.JudgeAborted):
        J.judge_responses(_ThrowBackend(), resps, buero_pack, max_consecutive_failures=3)

def test_judge_responses_resets_counter_on_success(buero_pack):
    resps = _resps(buero_pack, 5)
    be = _ThrowBackend(fail_first=2)  # 2 fail, then succeed → counter resets, no abort
    vs = J.judge_responses(be, resps, buero_pack, max_consecutive_failures=3)
    assert sum(1 for v in vs if v.judge_error) == 2
    assert sum(1 for v in vs if not v.judge_error) == 3

def test_judge_responses_does_not_persist_error_verdicts(buero_pack):
    resps = _resps(buero_pack, 1); persisted=[]
    J.judge_responses(_ThrowBackend(), resps, buero_pack, on_verdict=persisted.append)
    assert persisted == []  # error verdict NOT streamed to on_verdict (so resume re-judges)
```

Add a `buero_pack` fixture at the top of the test file:

```python
import pytest
from touchstone.pack import load_pack

@pytest.fixture
def buero_pack():
    return load_pack("packs/buero.yaml")
```

- [ ] **Step 2: Run → FAIL** (`TypeError: unexpected keyword 'max_consecutive_failures'`).

- [ ] **Step 3: Implement** — add the constant near the other rationales (~30) and rewrite `judge_responses`:

```python
JUDGE_ERROR_RATIONALE = "⚠ Judge-Fehler (nicht bewertbar): {err}"
```

```python
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
    *successful* verdict is passed to ``on_verdict``. A ``JudgeCallError`` (timeout/API) degrades
    that cell to an unscored ``judge_error`` verdict (NOT streamed to ``on_verdict`` → re-judged on
    resume); ``max_consecutive_failures`` (>0) consecutive failures raise ``JudgeAborted``."""
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
                _verdict(resp, prompt, score=0, red_flag=False,
                         rationale=JUDGE_ERROR_RATIONALE.format(err=e),
                         unscored=True, judge_error=True)
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
```

- [ ] **Step 4: Run → PASS** (`uv run pytest tests/test_judge_guard.py -q`).

- [ ] **Step 5: Lint/type/commit**

```bash
uv run ruff check touchstone/judge.py && uv run mypy touchstone/judge.py
git add -u && git commit -m "feat(judge): judge_responses degrade (judge_error) + consecutive-failure circuit-breaker"
```

---

### Task 5: `score_dimensions` degrade

**Files:**
- Modify: `touchstone/judge.py:265-270` (score_dimensions)
- Test: `tests/test_judge_guard.py`

**Interfaces:**
- Produces: `score_dimensions` returns a degraded `ModelReport` (empty `dim_scores`, `dim_rationales={"_error": …}`) instead of raising when the holistic call fails.

- [ ] **Step 1: Failing test**

```python
def test_score_dimensions_degrades_on_call_error(buero_pack):
    class _Throw:
        def judge(self, *, system, user): raise J.JudgeCallError("boom")
    rep = J.score_dimensions(_Throw(), buero_pack, model="m", variant="none", verdicts=[])
    assert rep.dim_scores == {}
    assert "_error" in rep.dim_rationales
```

- [ ] **Step 2: Run → FAIL** (`JudgeCallError` propagates).

- [ ] **Step 3: Implement** — wrap the call:

```python
def score_dimensions(
    backend: JudgeBackend, pack: Pack, *, model: str, variant: str, verdicts: list[Verdict]
) -> ModelReport:
    system, user = _build_dimension_prompt(pack, verdicts)
    try:
        raw = backend.judge(system=system, user=user)
    except JudgeCallError as e:
        return ModelReport(model=model, variant=variant, dim_scores={},
                           dim_rationales={"_error": JUDGE_ERROR_RATIONALE.format(err=e)})
    scores, rationales = parse_dimension_report(raw, pack)
    return ModelReport(model=model, variant=variant, dim_scores=scores, dim_rationales=rationales)
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**

```bash
uv run ruff check touchstone/judge.py && uv run mypy touchstone/judge.py
git add -u && git commit -m "feat(judge): score_dimensions degrades to error-report on judge call failure"
```

---

### Task 6: `judge_bundle` threads the threshold

**Files:**
- Modify: `touchstone/judge.py:273-303` (judge_bundle)
- Test: `tests/test_judge_guard.py`

**Interfaces:**
- Produces: `judge_bundle(backend, responses, pack, *, prior_verdicts=None, on_verdict=None, max_consecutive_failures: int = 0)` — passes the threshold to `judge_responses`.

- [ ] **Step 1: Failing test**

```python
def test_judge_bundle_threads_threshold(buero_pack):
    resps = _resps(buero_pack, 5)
    with pytest.raises(J.JudgeAborted):
        J.judge_bundle(_ThrowBackend(), resps, buero_pack, max_consecutive_failures=3)
```

- [ ] **Step 2: Run → FAIL** (`TypeError: unexpected keyword`).

- [ ] **Step 3: Implement** — add the param to `judge_bundle` and forward it:

```python
def judge_bundle(
    backend: JudgeBackend,
    responses: list[EvalResponse],
    pack: Pack,
    *,
    prior_verdicts: list[Verdict] | None = None,
    on_verdict: Callable[[Verdict], None] | None = None,
    max_consecutive_failures: int = 0,
) -> tuple[list[Verdict], list[ModelReport]]:
    prior = list(prior_verdicts or [])
    skip = {_verdict_key(v) for v in prior}
    fresh = judge_responses(
        backend, responses, pack, skip_keys=skip, on_verdict=on_verdict,
        max_consecutive_failures=max_consecutive_failures,
    )
    verdicts = prior + fresh
    groups: list[tuple[str, str]] = []
    for r in responses:
        if (r.model, r.variant) not in groups:
            groups.append((r.model, r.variant))
    reports = [
        score_dimensions(
            backend, pack, model=model, variant=variant,
            verdicts=[v for v in verdicts if v.model == model and v.variant == variant],
        )
        for model, variant in groups
    ]
    return verdicts, reports
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**

```bash
uv run ruff check touchstone/judge.py && uv run mypy touchstone/judge.py
git add -u && git commit -m "feat(judge): judge_bundle threads max_consecutive_failures to judge_responses"
```

---

### Task 7: CLI wiring (bounded backend + no-persist + JudgeAborted)

**Files:**
- Modify: `touchstone/cli.py:658-678` (`_judge_and_persist`), `:777` (backend), the `judge` command's call sites + try-block
- Test: covered by Task 4/6 logic; add a CLI-level smoke is out of scope (needs subprocess). Verify via full suite + a manual `--help`.

**Interfaces:**
- Consumes: `JudgeConfig.{call_timeout_s,max_tokens,max_consecutive_failures}`, `JudgeAborted`, `Verdict.judge_error`
- Produces: `_judge_and_persist(backend, responses, pk, prior, jpath, *, max_consecutive_failures: int, on_verdict=None)` — passes threshold to `judge_bundle`; clean rewrite filters `judge_error`.

- [ ] **Step 1: Edit `_judge_and_persist`** (cli.py ~658) — add the threshold param + persist filter:

```python
def _judge_and_persist(backend, responses, pk, prior, jpath, *, max_consecutive_failures,
                       on_verdict=None):
    with jpath.open("a", encoding="utf-8") as jh:
        def _append(v: Verdict) -> None:
            jh.write(json.dumps(v.as_dict(), ensure_ascii=False) + "\n")
            jh.flush()
            if on_verdict is not None:
                on_verdict(v)
        verdicts, reports = judge_bundle(
            backend, responses, pk, prior_verdicts=prior, on_verdict=_append,
            max_consecutive_failures=max_consecutive_failures,
        )
    with jpath.open("w", encoding="utf-8") as jh:  # clean rewrite: prior + new, deduped
        for v in verdicts:
            if v.judge_error:  # never persist error cells → a resume re-judges them
                continue
            jh.write(json.dumps(v.as_dict(), ensure_ascii=False) + "\n")
    return verdicts, reports
```

(Keep the existing type annotations on the params; only `judge_error` filter + threshold are new.)

- [ ] **Step 2: Edit the backend construction** (cli.py ~777):

```python
    backend = OpenAIJudgeBackend(
        jc.endpoint.base_url, jc.endpoint.api_key, jc.model, jc.temperature,
        timeout=jc.call_timeout_s, max_tokens=jc.max_tokens,
    )
```

- [ ] **Step 3: Pass the threshold at every `_judge_and_persist` call** in the `judge` command (both the plain and `--web`/emit paths). Each call gains `max_consecutive_failures=jc.max_consecutive_failures`. Import `JudgeAborted` from `touchstone.judge` (add to the existing import block at cli.py:31-39).

- [ ] **Step 4: Catch `JudgeAborted`** in the `judge` command's `try:` block (cli.py ~798) — add before the generic finalize, so the run aborts cleanly (the GUI-spawned sentinel still records `failed`):

```python
    except JudgeAborted as e:
        console.print(f"[red]Judge abgebrochen:[/] {e}")
        raise typer.Exit(code=1) from None
```

- [ ] **Step 5: Run full suite + lint + type + manual help**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy touchstone/ && uv run touchstone judge --help`
Expected: all PASS; `--help` lists the command.

- [ ] **Step 6: Commit**

```bash
git add -u && git commit -m "feat(judge): CLI wires guard (bounded backend, threshold, no-persist error cells, JudgeAborted catch)"
```

---

### Task 8: `judge.example.yaml` + AGENTS.md docs

**Files:**
- Modify: `judge.example.yaml`, `AGENTS.md`
- Test: `tests/test_judge_guard.py` (example loads)

**Interfaces:**
- Produces: documented defaults in `judge.example.yaml`.

- [ ] **Step 1: Failing test**

```python
def test_judge_example_has_guard_fields():
    from touchstone.judge import load_judge_config
    jc = load_judge_config("judge.example.yaml")
    assert jc.call_timeout_s > 0 and jc.max_consecutive_failures >= 0
```

- [ ] **Step 2: Run → FAIL** (if example lacks the keys, defaults still apply → test may PASS already; in that case make it assert the keys are *present* by reading the YAML):

```python
def test_judge_example_documents_guard_fields():
    import yaml
    raw = yaml.safe_load(open("judge.example.yaml", encoding="utf-8"))
    assert "call_timeout_s" in raw and "max_consecutive_failures" in raw and "max_tokens" in raw
```

- [ ] **Step 3: Add to `judge.example.yaml`:**

```yaml
call_timeout_s: 120          # per-Call-Timeout — fail fast statt ~30 min bei Runaway-Judges
max_consecutive_failures: 3  # Circuit-Breaker: so viele Fehler in Folge brechen den Lauf ab (0 = aus)
max_tokens: null             # optionaler Cap der Judge-Generierung; default aus
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: AGENTS.md gotcha** (nach dem Reasoning-only-Bullet):

> **Judge-Runaway-Guard:** Der Judge-Call ist gebunden (`call_timeout_s`, `max_retries=0`, optional `max_tokens`) und wirft `JudgeCallError` statt ~30 min zu hängen. `judge_responses` degradiert eine gescheiterte Zelle zu einem `unscored`+`judge_error`-Verdict (⚠-Begründung, **nicht** persistiert → Resume re-judged) und bricht nach `max_consecutive_failures` mit `JudgeAborted` ab (Thinking-Modelle als Judge drehen durch → nimm `qwen3.6-27b`, [[judge-thinking-model-runaway]]). CLI/GUI erben das automatisch.

- [ ] **Step 6: Full suite + commit**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy touchstone/
git add -A && git commit -m "feat(judge): judge.example.yaml guard defaults + AGENTS.md gotcha"
```

---

## Self-Review

**1. Spec coverage:** ① bounded backend ✓ (T2) · JudgeCallError ✓ (T2) · ② degrade per-answer ✓ (T4) · circuit-breaker + JudgeAborted ✓ (T4/T6) · score_dimensions degrade ✓ (T5) · Verdict.judge_error + no-persist ✓ (T1/T7) · JudgeConfig fields ✓ (T3) · CLI wiring + JudgeAborted catch ✓ (T7) · judge.example + AGENTS ✓ (T8). GUI inherits via CLI subprocess (no separate task — correct).
**2. Placeholder scan:** none. Test fakes are concrete.
**3. Type consistency:** `JudgeCallError`/`JudgeAborted`/`JUDGE_ERROR_RATIONALE`/`judge_error`/`max_consecutive_failures` consistent across T1–T7. `judge_responses`/`judge_bundle`/`_judge_and_persist` signatures consistent.

## Final verify (vor Merge)

`uv run pytest -q` · `ruff check . && ruff format --check .` · `mypy touchstone/` — alle grün. Dann fokussierte adversariale Whole-Branch-Review (Korrektheit/Regression/Tests), Findings fixen, `feat/judge-runaway-guard` → `main` mergen + pushen (solo, kein PR).
