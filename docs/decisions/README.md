# Decision Records (ADRs)

Die tragenden Entscheidungen des Harness als einzeln referenzierbare **Architecture Decision Records**
(MADR-leicht: Kontext · Entscheidung · Erwogene Alternativen · Auswirkungen · Belege). Format:
[`0000-template.md`](0000-template.md). Ergänzt die Diátaxis-„explanation"
([`../explanation/design-decisions.md`](../explanation/design-decisions.md), Verständnis-Narrativ) und die
dated SDD-Historie ([`../superpowers/specs/`](../superpowers/specs)).

## Tragende Entscheidungen

| ID | Titel | Bereich | Links |
|---|---|---|---|
| [0001](0001-openai-only.md) | OpenAI-kompatibel = einzige Schnittstelle | Architektur | [spec](../superpowers/specs/2026-06-19-ndeval-harness-design.md) · `client.py` |
| [0002](0002-entkoppelte-producer.md) | Zwei entkoppelte Producer + Merge per Zeitfenster | Architektur | [spec](../superpowers/specs/2026-06-19-ndeval-harness-design.md) · `sampler.py`,`merge.py` |
| [0003](0003-verteilung-statt-mittel.md) | Verteilung statt Mittelwert (P50/P95, CV%) | Perf-Methodik | [spec](../superpowers/specs/2026-06-19-ndeval-harness-design.md) · `stats.py`,`report.py` |
| [0004](0004-modell-delta.md) | Modell-Delta vs System-Peak | Perf-Methodik | `merge.py`,`report.py` |
| [0005](0005-warmup-cold-start.md) | Warmup verworfen, Cold-Start separat | Perf-Methodik | `runner.py` |
| [0006](0006-quali-eval-pack-als-daten.md) | Quali-Eval-Teilung (eval↔judge) + Pack=Daten | Quali-Eval | [spec](../superpowers/specs/2026-06-27-buero-pack-design.md) · `pack.py`,`qualrun.py` |
| [0007](0007-holistische-dimensionen.md) | Master-Dimensionen holistisch + Nachvollziehbarkeit | Judge | `judge.py`,`scorecard.py` |
| [0008](0008-ko-safety-red-flag-scope.md) | K.-o./Safety-Gate + red_flag_scope | Quali-Eval | [spec](../superpowers/specs/2026-06-27-ko-red-flag-scope-design.md) · `pack.py`,`scorecard.py` |
| [0009](0009-reasoning-only-unscored-preflight.md) | reasoning-only → unscored + Pre-Flight + free max_tokens | Judge | [spec](../superpowers/specs/2026-06-22-thinking-models-blocker-design.md) · `judge.py`,`preflight.py` |
| [0010](0010-judge-runaway-guard.md) | Judge-Runaway-Guard | Judge | [spec](../superpowers/specs/2026-06-28-judge-runaway-guard-design.md) · `judge.py`,`cli.py` |
| [0011](0011-thinking-suppression.md) | Thinking-Suppression (suppress_thinking) | Judge | [spec](../superpowers/specs/2026-06-28-judge-prompt-patch-design.md) · `judge.py` |
| [0012](0012-judge-prompt-haertung.md) | Judge-Prompt-Härtung (Rationale-Regeln + gescopte Safety-Reconciliation) | Judge | [spec](../superpowers/specs/2026-06-28-judge-prompt-patch-design.md) · `judge.py` |
| [0013](0013-judge-quality-meta-eval.md) | Judge-Quality-Meta-Eval-Methode | Judge | [spec](../superpowers/specs/2026-06-26-judge-quality-meta-eval-design.md) · `gui/judge_meta.py` |
| [0014](0014-gui-control-plane.md) | GUI Out-of-Process-Control-Plane + runs/=SSOT | GUI | [spec](../superpowers/specs/2026-06-21-gui-steuerzentrale-design.md) · `gui/` |
| [0015](0015-nacht-queue.md) | Nacht-Queue (ein Modell/Run, reset+settle, Watchdog) | Betrieb | [spec](../superpowers/specs/2026-06-27-nacht-queue-design.md) · `runqueue.py` |

## Backlog (spec-only — ADR-bar bei Bedarf)

Diese Entscheidungen sind in ihren Specs dokumentiert; ein ADR wird bei Bedarf nachgezogen.

| Thema | Bereich | Spec |
|---|---|---|
| Cross-Run-Aggregation | Perf-Methodik | [spec](../superpowers/specs/2026-06-20-cross-run-aggregation-design.md) |
| Web-Live-Monitor (`eval --web`) | GUI | [spec](../superpowers/specs/2026-06-20-web-live-monitor-design.md) |
| Judge-Web-Monitor (`judge --web`) | GUI | [spec](../superpowers/specs/2026-06-20-judge-web-monitor-design.md) |
| GUI-Nachvollziehbarkeit (Observability) | GUI | [spec](../superpowers/specs/2026-06-21-gui-nachvollziehbarkeit-design.md) |
| Modell-Auswahl-GUI | GUI | [spec](../superpowers/specs/2026-06-21-modell-auswahl-gui-design.md) |
| Modell-Vergleich (Ink. 8) | GUI | [spec](../superpowers/specs/2026-06-21-modell-vergleich-design.md) |
| Modell-Dropdown-Discovery | GUI | [spec](../superpowers/specs/2026-06-22-modell-dropdown-discovery-design.md) |
| GUI Best-Practices-Redesign | GUI | [spec](../superpowers/specs/2026-06-23-gui-best-practices-redesign-design.md) |
| B1: Result-Compare-Merge | GUI | [spec](../superpowers/specs/2026-06-24-b1-result-compare-merge-design.md) |
| Vergleichbarkeit-Fundament | Perf-Methodik | [spec](../superpowers/specs/2026-06-24-vergleichbarkeit-fundament-design.md) |
| B2: Cross-Run-Compare | GUI | [spec](../superpowers/specs/2026-06-25-b2-cross-run-compare-design.md) |
| B3a: Config-Start-Flow | GUI | [spec](../superpowers/specs/2026-06-25-b3a-config-start-flow-design.md) |
| Eval-Modell-Picker (Einzel) | GUI | [spec](../superpowers/specs/2026-06-25-eval-model-picker-single-design.md) |
| Pack-Editor | GUI | [spec](../superpowers/specs/2026-06-25-pack-editor-design.md) |
| Meta-Report (GUI E) | GUI | [spec](../superpowers/specs/2026-06-26-meta-report-design.md) |
| Overview + Batch-Trash | GUI | [spec](../superpowers/specs/2026-06-26-overview-batch-trash-design.md) |
| Cross-Judge-Aggregat | Judge | [spec](../superpowers/specs/2026-06-27-cross-judge-aggregate-design.md) |
| Judge-Meta-GUI | Judge | [spec](../superpowers/specs/2026-06-27-judge-meta-gui-design.md) |
