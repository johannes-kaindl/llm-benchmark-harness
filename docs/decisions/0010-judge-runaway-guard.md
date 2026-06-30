# ADR-0010: Judge-Runaway-Guard

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Judge

## Kontext

`OpenAIJudgeBackend.judge()` rief `chat.completions.create` ungestreamt auf — **ohne `max_tokens` und ohne expliziten Timeout**. Wird ein **Thinking-Modell** als Judge gewählt (z. B. `qwen/qwen3.6-35b-a3b`), denkt es auf einem einzelnen Call unbegrenzt weiter; jeder Call läuft in den SDK-Default-Timeout (600 s) × 2 Retries (≈ 30 min), der resultierende `APITimeoutError` blieb **ungefangen** und ließ den Judge-Prozess crashen (Exit ≠ 0). Beobachtet am 2026-06-28: 33 min bei 96 % GPU, 0 Verdicts, dann Crash; über die GUI sah es wie ein „verschwundener Run" aus.

Kräfte/Constraints: Ein schlechter Judge-Modell-Griff muss **schnell, sichtbar und nicht-destruktiv** scheitern (Sekunden statt 30 min, klare Meldung statt Crash, Run finalisiert oder bricht sauber ab). Bestehende `judge.yaml`-Configs und alte `judgements.jsonl` (ohne neue Felder) müssen unverändert weiterlaufen (Rückwärtskompatibilität). **Nicht-Ziel:** Thinking-Judges *nutzbar* zu machen — die Steuerung zu einem nicht-Thinking-Modell wie `qwen3.6-27b` bleibt via Docs/Memory.

## Entscheidung

Zwei Schichten in `touchstone/judge.py`:

1. **Bounded transport (Backend):** `OpenAIJudgeBackend` erhält `timeout`, `max_retries: int = 0` und `max_tokens`; der `OpenAI()`-Client wird mit `timeout`/`max_retries` konstruiert (Timeout nur wenn gesetzt geforwardet). Der SDK-Call wird in `try/except` gewrappt: ein `OpenAIError` wird zu der neuen, typisierten `JudgeCallError` umgemünzt (`raise JudgeCallError(...) from e`); ein Nicht-`OpenAIError` (Programmierfehler) wird durchgereicht statt als Judge-Fehler maskiert.
2. **Degrade + Circuit-Breaker (Orchestrierung):** `judge_responses` umschließt den `score_response`-Aufruf. Ein `JudgeCallError` degradiert die Zelle zu einem unscored Error-Verdict (`unscored=True, judge_error=True`, Rationale `⚠ Judge-Fehler (nicht bewertbar): {err}`) und zählt einen Folge-Fehler-Zähler hoch; ein Erfolg dazwischen setzt den Zähler zurück. Erreicht der Zähler `max_consecutive_failures` (>0), wird die neue `JudgeAborted` geworfen. Error-Verdicts werden **nicht** an `on_verdict` gestreamt und in `_judge_and_persist`s Clean-Rewrite herausgefiltert (`if v.judge_error: continue`), sodass ein Resume mit gutem Modell die Zelle neu bewertet. `score_dimensions` fängt `JudgeCallError` zusätzlich ab und liefert einen degradierten `ModelReport` (Defense-in-Depth).

Verdrahtung: `JudgeConfig` bekommt drei Felder mit Defaults — `call_timeout_s: float = 120`, `max_consecutive_failures: int = 3`, `max_tokens: int | None = None`. Der Judge-CLI-Command konstruiert das Backend mit `timeout=jc.call_timeout_s, max_tokens=jc.max_tokens`, reicht `max_consecutive_failures=jc.max_consecutive_failures` durch und fängt `JudgeAborted` → rote Meldung + `typer.Exit(code=1)` (sauberer Abbruch, kein Traceback). Die GUI nutzt den CLI-Subprozess und erbt das (Exit ≠ 0 → Sentinel `failed`).

## Erwogene Alternativen

- **`max_consecutive_failures` per Default als reiner `judge_bundle`-Parameter aktiv** — verworfen: `judge_bundle`/`judge_responses` defaulten auf `0` (Breaker aus), damit ein direkter `judge_bundle`-Aufruf ohne Config sicher bleibt; nur die CLI reicht den konfigurierten Wert (Default 3) ein.
- **`max_tokens`-Cap als primärer Guard (Default an)** — verworfen: ein Cap würde lange, legitime holistische Rationales abschneiden; der **Timeout** ist der primäre Guard, der Cap bleibt Opt-in (`max_tokens: null`) für Nutzer, die Compute hart deckeln wollen.
- **Error-Verdicts persistieren** — verworfen: dann überspränge ein Resume mit einem *guten* Modell die Zellen und sie blieben für immer „error"; daher Persistenz-Filter über `judge_error`.
- **Beliebige Exceptions im Backend zu `JudgeCallError` umwandeln** — verworfen: ein Nicht-`OpenAIError` ist ein Programmier-/Client-Bug und wird durchgereicht, nicht als Judge-Fehler maskiert.
- **Thinking-Judges nutzbar machen** — bewusstes Nicht-Ziel: gelöst über `qwen3.6-27b` via Docs/Memory, nicht im Guard.

## Auswirkungen

- Positiv: Ein durchdrehendes Judge-Modell scheitert in Sekunden statt ~30 min, mit klarer Meldung statt Crash; der Run finalisiert oder bricht sauber ab (Exit 1, GUI-Sentinel `failed`).
- Positiv: Verstreute Einzel-Call-Fehler degradieren nur einzelne Zellen (unscored, aus dem Mittel ausgenommen) und brechen den Run nicht ab; ein Erfolg resettet den Breaker.
- Positiv: Rückwärtskompatibel — bestehende `judge.yaml` und alte `judgements.jsonl` ohne `judge_error` laden unverändert (Feld defaultet auf `False`).
- Trade-off / Restgrenze: `max_tokens` ist standardmäßig aus (kein Truncating-Risiko), d. h. ohne Opt-in deckelt der Guard nur die Zeit, nicht die Tokenmenge.
- Trade-off / Restgrenze: Thinking-Judges bleiben unbrauchbar — der Guard macht ihren Fehlschlag nur schnell/sichtbar, behebt ihn aber nicht (Modellwahl bleibt Nutzer-/Doku-Verantwortung).

## Belege & Links
- Spec: `docs/superpowers/specs/2026-06-28-judge-runaway-guard-design.md` · Code: `touchstone/judge.py`, `touchstone/cli.py` · Tests: `tests/test_judge_guard.py`
- Verwandt: —
