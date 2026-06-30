# ADR-0011: Thinking-Suppression (suppress_thinking)

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Judge

## Kontext
Der empfohlene Judge ist ein lokales, dichtes/hybrides Reasoning-Modell (z. B. `qwen3.6-27b` auf LM Studio). Bei aktivem Reasoning lieferte ein solcher Judge nur einen Reasoning-Stream bzw. leeren `content` und sprengte den Call-Timeout — der Judge war damit unbrauchbar (Befund §5 der Meta-Eval: „Der Judge konnte Thinking nicht unterdrücken … lieferte nur Reasoning/leeren Content und sprengte den Call-Timeout"; verwandte Diagnose `qwen3.6-35b-a3b`: „33 min / 96 % GPU / 0 Verdicts"). Kräfte/Constraints: das Thinking muss server-seitig abgeschaltet werden, **ohne** Eingriff in LM Studio, und über heterogene OpenAI-kompatible Server hinweg (Ollama/vLLM/OpenAI-compat, llama.cpp, MLX, LM Studio, Qwen3), die je unterschiedliche, nicht-standardisierte Hint-Keys erwarten. Zugleich darf eine echte Cloud-OpenAI-Endpunkt-Nutzung nicht brechen (dort kennt `reasoning_effort` kein `"none"`).

## Entscheidung
Standardmäßig wird Thinking unterdrückt: `JudgeConfig.suppress_thinking: bool = True`. Ist der Wert gesetzt, hängt `OpenAIJudgeBackend.judge` per `extra_body` ein „Belt-and-suspenders"-Bündel (`SUPPRESS_THINKING_BODY`) an den Chat-Completion-Call: `{"reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}, "reasoning_budget": 0}`. Diese Keys decken die verschiedenen Server-Familien gleichzeitig ab und werden bewusst verbatim via `extra_body` gesendet, damit das SDK die nicht-standardisierten Keys nicht ablehnt. Der Flag wird vom CLI (`touchstone judge`) aus der Config an das Backend durchgereicht (`suppress_thinking=jc.suppress_thinking`). Für einen Cloud-Judge, der diese Hints ablehnt, ist `suppress_thinking: false` zu setzen.

## Erwogene Alternativen
- **Eingriff in LM Studio / Server-seitige Modell-Konfiguration** — verworfen, weil die Lösung „ohne LM-Studio-Eingriff" funktionieren musste (Befund §5: „behoben … ohne LM-Studio-Eingriff").
- **Nur ein einzelner Hint-Key (z. B. nur `reasoning_effort`)** — verworfen, weil die Server-Familien unterschiedliche Keys erwarten; der Kommentar im Code begründet das Bündel explizit als „Belt-and-suspenders … across servers", `reasoning_effort` für Ollama/vLLM/OpenAI-compat, `chat_template_kwargs` für llama.cpp/MLX/LM Studio/Qwen3, `reasoning_budget` für llama.cpp.
- **Suppression generell erzwingen (kein Opt-out)** — verworfen, weil echtes Cloud-OpenAI `reasoning_effort: "none"` nicht kennt; daher der abschaltbare Default statt einer harten Erzwingung.
- **Thinking laufen lassen und nur per `max_tokens` kappen** — verworfen; `max_tokens` ist defaultmäßig aus (`None`, „no truncation risk") und ein gekappter Reasoning-Stream liefert weiterhin keinen parseable Verdict.

## Auswirkungen
- Positiv: ein hybrides Reasoning-Modell emittiert einen parseable Verdict statt eines Runaway-Reasoning-Streams; der lokale Default-Judge (`qwen3.6-27b`) wird offline nutzbar, ohne den Call-Timeout zu sprengen (Enabler für den gemessenen A/B-Prompt-Patch in §5).
- Positiv: ein einziger Default deckt alle gängigen OpenAI-kompatiblen Server ab; kein server-spezifisches Setup nötig.
- Trade-off / Restgrenze: für einen Cloud-Judge, der die Hints ablehnt (echtes OpenAI: `reasoning_effort` ohne `"none"`), muss `suppress_thinking` bewusst auf `false` gesetzt werden — der Default ist auf den lokalen Judge optimiert.
- Trade-off / Restgrenze: die nicht-standardisierten Keys werden roh via `extra_body` durchgereicht; ihre Wirkung hängt vom konkreten Server-Passthrough ab und ist nicht über das SDK validiert.

## Belege & Links
- Spec: `docs/superpowers/specs/2026-06-28-judge-prompt-patch-design.md` · Code: `touchstone/judge.py` (`SUPPRESS_THINKING_BODY`, `JudgeConfig.suppress_thinking`, `OpenAIJudgeBackend`), `touchstone/cli.py` (`judge`-Command, `suppress_thinking=jc.suppress_thinking`) · Explanation: `docs/explanation/judge-quality-meta-eval-2026-06-28.md` (§5, Nebenbefund/Enabler) · Tests: `tests/test_judge.py`
- Verwandt: ADR-0010 (Judge-Runaway-Guard — Fail-fast/Circuit-Breaker für denselben lokalen Judge)
