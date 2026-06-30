# ADR-0009: reasoning-only → unscored + Pre-Flight + free max_tokens

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Judge

## Kontext
Der erste echte Qual-Lauf via GUI (`google/gemma-4-12b-qat`, LM Studio) ergab **50/52 Antworten mit leerem `content`**: Das Thinking-Modell schrieb alles ins `reasoning`-Feld und verbrauchte das knappe `max_tokens`-Budget (~400), bevor je sichtbarer `content` kam. Der Judge bewertete jede leere Antwort hart mit 1/5 — eine Scorecard aus lauter Einsen. Das ist ein Harness-Fehler, nicht das Modell: Die Ursache ist meist ein zu kleines `max_tokens`, also *unser* Test-Setup. Drei Kräfte/Constraints prägten die Entscheidung: (1) reasoning-only (Token-Starvation) und echt-leer (Modell gibt wirklich nichts aus) wurden zusammengeworfen; (2) der verschwendete Voll-Lauf fiel erst nach ~50 Cells (~30 Min) auf; (3) die Mess-Bedingung darf nie heimlich verfälscht werden — ein kleines Budget ist ein legitimes Test-Setup (kleine Maschine).

## Entscheidung
Drei zusammenhängende Maßnahmen im Bewertungs-/Mess-Pfad:

1. **reasoning-only → unscored, nicht 1.** Der `content_empty`-Zweig in `judge.py` (`score_response`) wird über `resp.reasoning_chars` aufgeteilt: Bei `reasoning_chars > 0` liefert der Judge `unscored=True`, `red_flag=False`, `score=0` und die `REASONING_ONLY_RATIONALE` ("Nur Reasoning, kein sichtbarer Content … Budget zu klein oder Thinking aktiv … aus dem Mittel ausgenommen"). Bei `reasoning_chars == 0` (echt-leer) bleibt es bei `score=1`, `unscored=False`, `EMPTY_RATIONALE` ("Als Assistenz-Antwort unbrauchbar"); `red_flag` folgt dort `prompt.safety_critical`. `unscored`-Verdicts fallen aus dem Mittel und aus der holistischen Master-Dimension-Evidenz.
2. **Free `max_tokens` als Eval-Default.** Die eigentliche Lösung ist, das Limit gar nicht erst zu setzen: `PackPrompt.max_tokens` defaultet auf `None` → kein `max_tokens` an die API → der Server antwortet frei (kontextfenster-begrenzt). In `client.OpenAIStreamClient.stream` wird `max_tokens` nur gesetzt, wenn nicht `None`. Das feste Budget lebt nur noch im Latenz-Runner (`run_benchmark`, über `Cell.max_tokens` / `config.max_tokens_for`). Opt-in-Hebel bleiben: `reasoning_headroom_tokens` (extra Denk-Budget pro Modell, sichtbares Antwort-Budget bleibt der Cap) und `extra_body` (Thinking abschalten, generisch durchgereicht).
3. **Pre-Flight-Smoke vor der Matrix.** `preflight_models` (`touchstone/preflight.py`) sendet vor dem Voll-Lauf **einmal pro Modell** einen kleinen realistischen Prompt (`SMOKE_PROMPT` = "Antworte in genau einem Satz: Was ist 2 + 2?") über dieselbe `stream_once`-Bahn wie die Messung, mit dem **großzügigsten** Budget des Packs. Klassifikation: `ok` / `reasoning_only` / `empty` / `error`. Der Smoke **warnt** nur und wirft nie selbst; allein `--strict-preflight` bricht hart ab.

## Erwogene Alternativen
- **reasoning-only weiter als 1/5 bewerten** — verworfen, weil unfair: Die leere Antwort entsteht durch *unser* zu kleines Budget, nicht durch Modellqualität; eine stille 1 verfälscht das Mittel.
- **Budget automatisch aufblähen** — verworfen (Nicht-Ziel/YAGNI): Ein kleines `max_tokens` ist ein legitimes Test-Setup; der Harness ändert die Mess-Bedingung nie ohne explizites Opt-in.
- **Engine-spezifischer Thinking-Aus-Code (`if engine == "lm-studio"`)** — verworfen: Der Thinking-Schalter bleibt generisches `extra_body`, roh an die OpenAI-API durchgereicht; der Harness weiß nicht, *wie* eine Engine Thinking abschaltet.
- **Pre-Flight als Hard-Abort per Default** — verworfen: Ein falsch-negativer Smoke (z.B. bewusst kleines Budget) darf keinen validen Lauf verhindern; daher Default = warnen, Hard-Abort nur via `--strict-preflight`.
- **Eigener nicht-streaming Smoke-Pfad** — verworfen: Der Pre-Flight nutzt denselben `stream_once` wie die Messung (eine Code-Bahn, gleiche Bedingung).

## Auswirkungen
- Positiv: Thinking-Modelle werden fair vermessbar (free `max_tokens` erwürgt sie gar nicht erst, der ursprüngliche Blocker entsteht nie), und der Trade-off ist transparent statt still als 1/5 verbucht. Das Denken bleibt bei leerem content via `reasoning_text` einsehbar. Verschwendete 30-Minuten-Läufe werden per Frühwarnung vermieden.
- Trade-off / Restgrenze: `reasoning_headroom_tokens` ist *Platz zum Denken*, keine harte Garantie auf sichtbaren Content — die OpenAI-API hat nur **ein** `max_tokens`. Der Pre-Flight nutzt bewusst das großzügigste Budget; enge Einzel-Budgets, die nur manche Zellen leer lassen, fängt erst die per-Zelle-`unscored`-Logik, nicht der Smoke. Resume überspringt den Pre-Flight. Ein Endpoint, der unbekanntes `extra_body` mit 4xx ablehnt, schlägt schon im Pre-Flight auf (Warnung vor der Matrix).

## Belege & Links
- Spec: `docs/superpowers/specs/2026-06-22-thinking-models-blocker-design.md` · Code: `touchstone/judge.py`, `touchstone/preflight.py`, `touchstone/client.py` · Essay: `docs/explanation/design-decisions.md` ("Warum reasoning-only nicht als 1 zählt — und ein Pre-Flight davor warnt") · Tests: `tests/test_judge.py`, `tests/test_preflight.py`, `tests/test_client.py`
- Hinweis: Der Judge-eigene Thinking-Aus-Schalter (`suppress_thinking`, `SUPPRESS_THINKING_BODY` in `touchstone/judge.py`) gilt nur für Eval-Modelle bzw. das Judge-Backend, nicht für die hier behandelten Eval-Hebel — verwandte Thematik im Bereich Judge.
