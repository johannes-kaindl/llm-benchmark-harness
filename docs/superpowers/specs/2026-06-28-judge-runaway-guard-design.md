# Spec: Judge-Runaway-Guard

**Datum:** 2026-06-28 · **Status:** freigegeben (Johannes: „ok, gerne autonom so umsetzen") · **Projekt:** Judge-Härtung

## Problem

`OpenAIJudgeBackend.judge()` macht ein nicht-gestreamtes `chat.completions.create` **ohne
`max_tokens` und ohne expliziten Timeout**. Ein als Judge gewähltes **Thinking-Modell** (z. B.
`qwen/qwen3.6-35b-a3b`) dreht auf einem Judge-Call durch (denkt unbegrenzt) → jeder Call läuft in
den SDK-Default-Timeout (600s) × 2 Retries ≈ 30 min → `APITimeoutError` **ungefangen** → der
Judge-Prozess crasht (Exit ≠ 0). Beobachtet 2026-06-28: 33 min bei 96 % GPU, 0 Verdicts, dann
Crash; über die GUI sah es wie ein „verschwundener Run" aus ([[judge-thinking-model-runaway]]).

Der Guard soll einen schlechten Judge-Modell-Griff **schnell, sichtbar und nicht-destruktiv**
scheitern lassen — Sekunden statt 30 min, klare Meldung statt Crash, Run finalisiert oder bricht
sauber ab. **Nicht-Ziel:** Thinking-Judges *nutzbar* machen (die Steuerung zu `qwen3.6-27b` bleibt
via Docs/Memory).

## Lösung: zwei Schichten in `touchstone/judge.py`

### ① Bounded transport (Backend)

`OpenAIJudgeBackend.__init__` bekommt `timeout: float | None = None`, `max_retries: int = 0`,
`max_tokens: int | None = None`. Der `OpenAI()`-Client wird mit `timeout` + `max_retries`
konstruiert (nur wenn gesetzt forwarden — analog `OpenAIStreamClient`). `judge()` reicht
`max_tokens` an den Call (nur wenn gesetzt) und **wrappt den SDK-Call**:

```python
try:
    resp = self._client.chat.completions.create(...)
except Exception as e:                      # openai.APITimeoutError / APIError / Verbindung
    raise JudgeCallError(f"{type(e).__name__}: {e}") from e
```

`JudgeCallError(Exception)` ist neu und typisiert — die Orchestrierung fängt **genau diese**, nicht
beliebige Bugs.

### ② Degrade + Circuit-Breaker (Orchestrierung)

- **`score_response` bleibt unverändert** — es ruft `backend.judge()`, fängt nichts; eine
  `JudgeCallError` propagiert hinaus. (Seine bestehenden Nicht-Call-Pfade — reasoning-only,
  empty, not-ok, unparsebar — werfen nicht und bleiben wie sie sind.)
- **`judge_responses` umschließt den `score_response`-Aufruf:**
  ```
  try:
      verdict = score_response(backend, resp, prompt, pack)
      consecutive = 0
  except JudgeCallError as e:
      verdict = _verdict(resp, prompt, score=0, red_flag=False,
                         rationale=JUDGE_ERROR_RATIONALE.format(err=e), unscored=True)
      consecutive += 1
      if max_consecutive_failures and consecutive >= max_consecutive_failures:
          on_verdict?(verdict); verdicts.append(verdict)
          raise JudgeAborted(f"{consecutive} Judge-Calls in Folge gescheitert — "
                             f"Judge-Modell unbrauchbar (nimm ein nicht-Thinking-Modell wie qwen3.6-27b). Letzter Fehler: {e}")
  ```
  `JUDGE_ERROR_RATIONALE = "⚠ Judge-Fehler (nicht bewertbar): {err}"`. Der Zähler zählt **nur**
  echte Call-Fehler (nicht reasoning-only/unparsebar). `JudgeAborted(Exception)` ist neu.

  **Error-Verdicts werden NICHT persistiert** (sonst überspränge ein Resume mit einem *guten*
  Modell die Zellen und sie blieben für immer „error"). Dafür neu: `Verdict.judge_error: bool =
  False` (backward-kompatibel; alte judgements.jsonl ohne das Feld → `False`). Ein Error-Verdict
  setzt `unscored=True, judge_error=True`. Folgen:
  - `judge_responses` hängt das Error-Verdict an die **In-Memory-Liste** (für die aktuelle
    Scorecard), ruft aber `on_verdict` **nicht** (kein Append-Stream-Persist).
  - `_judge_and_persist`s Clean-Rewrite filtert `if not v.judge_error` → judgements.jsonl enthält
    nie Error-Zellen → beim Resume neu bewertet.
  - `unscored=True` sorgt für Mittelwert-Ausschluss (bestehende Mechanik); `judge_error` ist die
    klare, robuste Unterscheidung zu reasoning-only-unscored (Persistenz-Filter **und**
    Sichtbarkeit im Report/GUI).
- **`score_dimensions` (holistisch) fängt `JudgeCallError`** → degradierter `ModelReport`
  (leere `dim_scores`/`dim_rationales` + eine Notiz). Da der per-Antwort-Pass **vor** dem
  holistischen läuft, trippt ein Runaway-Modell den Breaker schon dort (≈3×timeout) und erreicht
  die Holistik gar nicht; der Catch hier ist Defense-in-Depth gegen einen holistik-spezifischen
  Hänger.

### Verdrahtung (CLI + GUI erben automatisch)

- **`JudgeConfig`** (judge.yaml) bekommt — alle mit Defaults, bestehende Configs unverändert:
  ```yaml
  call_timeout_s: 120           # per-Call-Timeout (float)
  max_consecutive_failures: 3   # Circuit-Breaker; 0 = aus
  max_tokens: null              # optionaler Cap (default aus — kein Truncating-Risiko)
  ```
- **`judge_bundle` / `judge_responses`** bekommen `max_consecutive_failures: int`-Parameter
  (Default `0` = aus, damit der reine `judge_bundle`-Aufruf ohne Config sicher bleibt; der CLI
  reicht `jc.max_consecutive_failures` durch).
- **`cli.py:777`** konstruiert `OpenAIJudgeBackend(..., timeout=jc.call_timeout_s,
  max_tokens=jc.max_tokens)`; `_judge_and_persist` reicht `max_consecutive_failures` an
  `judge_bundle`.
- Der **CLI-Judge-Command** (bestehender `try`-Block) fängt **`JudgeAborted`** → rote Meldung +
  `raise typer.Exit(1)` (sauberer Abbruch, kein Traceback). Die GUI nutzt den CLI-Subprozess →
  erbt das (Exit ≠ 0 → Sentinel `failed`, sichtbar).

## Betroffene/neue Symbole

- **neu** `JudgeCallError(Exception)`, `JudgeAborted(Exception)`, `JUDGE_ERROR_RATIONALE` in `judge.py`.
- **neu** `Verdict.judge_error: bool = False` in `results.py` (+ `as_dict`); `_judge_and_persist`
  Clean-Rewrite filtert `judge_error`-Verdicts raus (kein Persist → Resume re-judged).
- `OpenAIJudgeBackend.__init__`/`.judge` — timeout/max_retries/max_tokens + try-wrap.
- `JudgeConfig` — 3 neue Felder.
- `judge_responses` / `judge_bundle` — `max_consecutive_failures`-Param + try/except-Loop.
- `score_dimensions` — try/except → degradierter Report.
- `touchstone/cli.py` — Backend-Konstruktion + `_judge_and_persist`/`judge_bundle`-Param + `JudgeAborted`-Catch.
- `judge.example.yaml` — die 3 neuen Felder als dokumentierte Defaults.
- `AGENTS.md` — ein Gotcha (Judge-Guard: timeout/Breaker/keine Retries; Thinking-Judge scheitert schnell+sichtbar).

## Tests (DI-Ethos, ohne Server)

- `OpenAIJudgeBackend.judge` mit Fake-OpenAI-Client der `APITimeoutError` wirft → `JudgeCallError`;
  Erfolgsfall gibt Content zurück; `max_tokens`/`timeout` werden an Client/Call gereicht (nur wenn gesetzt).
- `judge_responses` mit Fake-Backend, das immer `JudgeCallError` wirft → Verdicts sind
  unscored+`⚠ Judge-Fehler`, und nach `max_consecutive_failures` wird `JudgeAborted` geworfen;
  mit Threshold `0` läuft es durch (alle unscored); ein Erfolg dazwischen **resettet** den Zähler
  (kein Abbruch bei verstreuten Einzelfehlern).
- `score_dimensions` mit werfendem Fake-Backend → degradierter Report (keine Exception nach außen).
- Error-Verdicts: `judge_error=True` + `unscored=True`; `on_verdict` wird für sie **nicht**
  gerufen; `_judge_and_persist`-Rewrite schreibt sie nicht → eine Resume-Runde re-judged die Zelle.
- `JudgeConfig`-Defaults: judge.example.yaml lädt; alte Config ohne die Felder lädt unverändert.
- Bestehende judge-Tests bleiben grün (Verhalten ohne Fehler unverändert).

## Offene Mini-Entscheidung (Umsetzung)

`max_tokens` default `null` (kein Cap): der **Timeout** ist der primäre Guard; ein Cap würde lange
legitime holistische Rationales abschneiden. Opt-in für Nutzer, die Compute hart deckeln wollen.
