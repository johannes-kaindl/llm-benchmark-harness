# ADR-0008: K.-o./Safety-Gate + red_flag_scope

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Quali-Eval

## Kontext

Das Safety-Gate `scorecard.passes_ko` war bewusst sicherheits-konservativ: **jeder** Judge-Red-Flag auf **irgendeinem** Prompt löst den K.-o. aus (gepinnt durch `test_any_red_flag_knocks_out_even_uncurated`). Für den Sicherheits-Pack `ndassist` ist das richtig. Für einen **Qualitäts**-Pack wie `buero` ist es zu hart: viele Red-Flags sind nicht gefährlich (Ton: E5 patzig; Format: C5/B4; Geschwafel: D4). Über 25 Prompts × 2 Varianten trifft praktisch jedes Modell *einen* solchen Qualitäts-Red-Flag, landet auf „nicht empfehlenswert", und die Rangliste verliert an der Spitze die Trennschärfe.

Leitprinzip: Halluzination im Büro-Kontext rechtfertigt Disqualifikation — ein nicht-gefährlicher Mangel soll dagegen nur Punkte kosten, nicht disqualifizieren. Constraints: Das bestehende `ndassist`-Verhalten muss byte-für-byte gleich bleiben (Backward-Kompatibilität), die Lösung muss zentral und scope-aware in `passes_ko` sitzen, und Sicherheit darf nicht durch einen hohen Gesamt-Score aufwiegbar werden (das Gate läuft unabhängig vom Score).

## Entscheidung

Ein optionales per-Pack-Feld `red_flag_scope: Literal["all", "curated"] = "all"` auf `KoRule` (`touchstone/pack.py`), ausgewertet zentral in `passes_ko` (`touchstone/scorecard.py`):

- **`all` (Default):** unverändert — jeder Red-Flag knockt aus; kuratierte Prompts werden in der Begründung zuerst genannt (`fatal = curated + sorted(red_flagged − red_flag_prompts)`). Packs ohne das Feld (`ndassist`) bleiben verhaltensgleich.
- **`curated`:** der Red-Flag-Zweig feuert **nur** für Prompts aus `red_flag_prompts` (`fatal = curated`). Andere Red-Flags senken den Score, lösen aber keinen K.-o. aus.

Der zweite Zweig — der Dimensions-Boden (`dim_scores[ko.dimension] ≤ ko.threshold`, Default `threshold=2`) — bleibt unverändert und scope-unabhängig in beiden Modi aktiv. Unter `curated` erzwingt ein Load-Time-Validator in `Pack._cross_references`, dass jeder `safety_critical`-Prompt in `ko_rule.red_flag_prompts` enthalten ist (sonst `ValueError`), damit dessen K.-o. nicht still ausgehebelt wird. `packs/buero.yaml` setzt `red_flag_scope: curated`.

## Erwogene Alternativen

- **Alles beim konservativen `all`-Default belassen** — verworfen, weil über 25 Prompts × 2 Varianten fast jedes Modell einen harmlosen Qualitäts-Red-Flag (Ton/Format/Geschwafel) trifft und damit die Rangliste an der Spitze ihre Trennschärfe verliert.
- **Generischer Pack-Validator „unter `curated` muss `safety_critical ⊆ red_flag_prompts`" als breites Feature** — als bewusst eng gehaltene Load-Time-Prüfung umgesetzt; ein darüber hinausgehender generischer Guard wurde als YAGNI zurückgestellt, bis ein zweiter `curated`-Pack existiert (`buero` erfüllt die Bedingung, ein Test pinnt sie).
- **Anzeige-Lücke des `all`-Scopes in der GUI mitbeheben** (nicht-kuratierte Red-Flags in `hit_prompts` benennen) — verworfen / out of scope, weil es eine vorbestehende, separate Display-Frage ist und `ndassist`-Verhalten unangetastet bleiben soll.

## Auswirkungen

- Positiv: Qualitäts-Packs (`buero`) erhalten Trennschärfe — harmlose Mängel kosten Punkte statt zu disqualifizieren, während Halluzination weiter doppelt hart gefasst ist (kuratierter Confabulations-Bait → K.-o.; pervasives Halluzinieren über den Dimensions-Boden → K.-o.).
- Positiv: vollständig backward-kompatibel — Packs ohne das Feld (`ndassist`) bleiben verhaltensgleich; die Logik ist zentral in `passes_ko`, alle Call-Sites reichen `pack` bereits durch.
- Trade-off / Restgrenze: Unter `all` kann der K.-o. durch einen *nicht*-kuratierten Red-Flag feuern, ohne dass die GUI-`hit_prompts` ihn nennt — eine bewusst nicht behandelte Anzeige-Lücke. Außerdem verlagert `curated` Verantwortung in die Pack-Daten: die Kuratierung von `red_flag_prompts` muss korrekt sein (der Load-Time-Validator deckt nur `safety_critical`-Abdeckung ab, nicht die fachliche Richtigkeit der Auswahl).

## Belege & Links

- Spec: `docs/superpowers/specs/2026-06-27-ko-red-flag-scope-design.md` · Code: `touchstone/pack.py`, `touchstone/scorecard.py` · Tests: `tests/test_pack.py`, `tests/test_scorecard.py` (`test_any_red_flag_knocks_out_even_uncurated`)
- Verwandt: ADR-XXXX
