---
paths:
  - "touchstone/judge.py"
  - "touchstone/scorecard.py"
  - "touchstone/gui/configs.py"
  - "tests/test_judge.py"
  - "tests/test_judge_guard.py"
  - "judge.example.yaml"
  - "judge.yaml"
---

- **Judge-Runaway-Guard.** Der Judge-Call ist gebunden — `OpenAIJudgeBackend` nimmt `timeout`
  (`JudgeConfig.call_timeout_s`, Default 120 s) + `max_retries=0` + optionalen `max_tokens` und wirft
  `JudgeCallError` statt ~30 min zu hängen. `judge_responses` degradiert eine gescheiterte Zelle zu
  einem `unscored`+`judge_error`-Verdict (⚠-Begründung) — **nicht** an `on_verdict` gestreamt und im
  Clean-Rewrite **nicht** persistiert, damit ein Resume sie neu bewertet — und bricht nach
  `max_consecutive_failures` (Default 3) mit `JudgeAborted` ab (der CLI fängt das → rote Meldung +
  Exit 1, Sentinel `failed`). Grund: **Thinking-Modelle als Judge drehen durch** (kein Cap, Thinking
  nie aus) → nimm ein dense Modell wie `qwen3.6-27b` ([[judge-thinking-model-runaway]]). `score_dimensions`
  fängt den Fehler ebenfalls (degradierter Report). CLI **und** GUI erben den Guard (die GUI spawnt den
  CLI-Subprozess); die einzige Backend-Konstruktion ist `cli.py`. Der Backend-`except` reicht
  Nicht-`OpenAIError` (Programmierfehler) durch, statt sie als Judge-Fehler zu maskieren. **Bekannte
  Grenze (selten):** schlägt *nur* der holistische Master-Call fehl (Per-Antwort-Pass war ok), wird ein
  degradierter `_error`-Report mit `dim_scores={}` geschrieben und der Lauf endet sauber (Exit 0) — die
  Nacht-Queue sieht `reports.jsonl` und re-judged ihn nicht automatisch; ein manuelles `judge` heilt ihn.

- **Judge model is pickable like the eval model.** `gui/configs.discover_models` is the shared discovery
  core; `discover_judge_endpoint_models` + `GET /judge-endpoint-models` (never-500, `judge*.yaml` path-guard)
  feed a GUI dropdown; `judge --judge-model <id>` overrides the YAML model for one run (no write-back),
  mirroring eval's `--models-json`.
