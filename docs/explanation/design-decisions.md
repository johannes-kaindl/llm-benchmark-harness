# Explanation — warum es so gebaut ist

Diese Seite ist das **Verständnis-Narrativ** (Diátaxis „explanation"): die große Linie, warum der Harness
diese Form hat. Das **Detail je Entscheidung** — Kontext, erwogene Alternativen, Auswirkungen — lebt als
einzeln referenzierbare Records unter [`../decisions/`](../decisions/README.md); jeder Absatz hier verlinkt
seinen ADR.

## Performance-Messung

Auf Apple-Silicon ist der Engpass der vereinheitlichte Speicher; an der RAM-Schwelle springt die Latenz.
Darum **Verteilung statt Mittelwert** (P50/P95, CV%) statt eines verwischenden Schnitts
([ADR-0003](../decisions/0003-verteilung-statt-mittel.md)). Speicher/Throttling werden **nie** aus dem
Request-Thread geschätzt, sondern von einem **entkoppelten Host-Sampler** gemessen und per Zeitfenster
gemergt ([ADR-0002](../decisions/0002-entkoppelte-producer.md)). Die maschinen-vergleichbare Speicherzahl
ist das **Modell-Delta** (Peak − Baseline), nicht der nackte System-Peak
([ADR-0004](../decisions/0004-modell-delta.md)). Warmup wird verworfen, der **Cold-Start** separat
ausgewiesen ([ADR-0005](../decisions/0005-warmup-cold-start.md)). Berichtet wird gegen die echten
`prompt_tokens` aus `usage`, nicht gegen den Ziel-Bucket (Tokenizer variieren pro Modell; Spalten-Detail:
[`../reference/metrics-and-schema.md`](../reference/metrics-and-schema.md)).

## Schnittstelle

**OpenAI-kompatibel ist die einzige Schnittstelle** — kein engine-spezifischer Code außer `client.py`, der
Maschinenwechsel (M1 ↔ M5) ist ein Config-Tausch ([ADR-0001](../decisions/0001-openai-only.md)).

## Qualitäts-Eval

Generierung (deterministisch, auf der Maschine) ist sauber von Bewertung (LLM/Mensch, danach) getrennt; ein
Use-Case ist ein **Pack = Daten, kein Code** ([ADR-0006](../decisions/0006-quali-eval-pack-als-daten.md)).
Die Master-Dimensionen werden **holistisch** bewertet; die Nachvollziehbarkeit trägt die belegte Begründung
(zitierte `prompt_id`s), nicht eine erfundene Dimension→Prompt-Matrix
([ADR-0007](../decisions/0007-holistische-dimensionen.md)). Das **Sicherheits-K.-o.** hat zwei unabhängige
Wurzeln (Dimensions-Boden + Red-Flag) mit konfigurierbarem `red_flag_scope`
([ADR-0008](../decisions/0008-ko-safety-red-flag-scope.md)).

## Judge-Härtung (Thinking-Modelle)

Ein „Thinking"-Modell kann sein Budget ins Reasoning schreiben → leerer Content. Das zählt **nicht als 1**,
sondern `unscored`; ein Pre-Flight warnt vorab, und die Eval lässt Modelle per Default frei antworten
([ADR-0009](../decisions/0009-reasoning-only-unscored-preflight.md)). Ein **Runaway-Guard** bindet jeden
Judge-Call (Timeout · keine Retries · Circuit-Breaker), damit ein durchdrehendes Modell den Lauf nicht hängt
([ADR-0010](../decisions/0010-judge-runaway-guard.md)). Thinking wird zudem aktiv **unterdrückt**
(`suppress_thinking` via `extra_body`), sodass ein hybrides Modell ohne LM-Studio-Eingriff parsebaren
Content liefert ([ADR-0011](../decisions/0011-thinking-suppression.md)). Der holistische Judge-Prompt ist
gegen Begründungs-Defekte gehärtet (belegte Beobachtung · Score-Höhe begründen · Fix benennen ·
sicherheits-gescopte Reconciliation) ([ADR-0012](../decisions/0012-judge-prompt-haertung.md)). Und der Judge
selbst ist **bewertbar**: eine Meta-Eval misst Kalibrierung + Begründungs-Qualität
([ADR-0013](../decisions/0013-judge-quality-meta-eval.md)).

## GUI & Betrieb

Die GUI ist ein **out-of-process Control-Plane** — sie spawnt die Messung als Subprozess und misst nie im
eigenen Prozess; `runs/` bleibt SSOT ([ADR-0014](../decisions/0014-gui-control-plane.md)). Die
**Nacht-Queue** fährt viele Modelle sequenziell als isolierte Subprozesse (ein Modell/Run, reset+settle,
Watchdog) ([ADR-0015](../decisions/0015-nacht-queue.md)).
