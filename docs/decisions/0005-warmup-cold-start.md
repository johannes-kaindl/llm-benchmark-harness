# ADR-0005: Warmup verworfen, Cold-Start separat

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Perf-Methodik

## Kontext
Der erste Request nach Server- bzw. Modell-Start zahlt einmalige Kosten — Gewichte mappen, Caches warm machen. Diese einmalige Last fällt nicht in dieselbe Größenordnung wie die folgenden, eingeschwungenen Anfragen. Mischt man sie in die berichtete Verteilung, verzerrt sie genau die Konsistenzmaße, auf die der Harness ausgelegt ist: P95 und CV%. Zugleich ist diese Einmal-Last selbst eine eigene, legitime Frage — „bleibt das Modell warm?" — die nicht verloren gehen, aber auch nicht die laufende Latenz-Verteilung kontaminieren soll.

## Entscheidung
Pro Zelle wird genau **ein** Warmup-Lauf durchgeführt und aus der Auswertung verworfen (er bleibt roh erhalten und ist über das Feld `warmup` markiert). Die Cold-Start-Latenz wird **einmal** am Anfang des gesamten Laufs gemessen und gesondert ausgewiesen (`is_cold_start=true`, eigene `run_id` `cold-<ci>`). Im Code: `run_benchmark` misst beim allerersten Request einmalig den Cold-Start (`cold_done`-Guard) und markiert je Zelle den ersten der `runs_per_cell` Messläufe als Warmup (`warmup=(ri == 0)`).

## Erwogene Alternativen
- **Cold-Start und Warmup in die Verteilung mischen (kein Verwerfen)** — verworfen, weil die einmaligen Lade-/Aufwärmkosten P95 und CV% verzerren und so die Konsistenzaussage des Harness unbrauchbar machen würden.
- **Cold-Start gar nicht messen / verschweigen** — verworfen, weil er eine eigene, relevante Frage beantwortet („bleibt das Modell warm?"); er wird daher nicht weggeworfen, sondern separat als eigener Datenpunkt ausgewiesen.

## Auswirkungen
- Positiv: Die berichtete Latenz-Verteilung (insbesondere P95 und CV%) bildet den eingeschwungenen Zustand sauber ab, statt von Einmal-Kosten verfälscht zu werden.
- Positiv: Die Cold-Start-Frage bleibt als eigener, gesondert ausgewiesener Datenpunkt erhalten und beantwortbar.
- Trade-off / Restgrenze: Pro Zelle geht ein Lauf (das Warmup) für die Auswertung verloren; er kostet Laufzeit, wird bewusst verworfen und nur roh/markiert behalten.

## Belege & Links
- Spec: `docs/superpowers/specs/2026-06-19-ndeval-harness-design.md` · Code: `touchstone/runner.py` · Essay: `docs/explanation/design-decisions.md`
- Verwandt: —
