# ADR-0003: Verteilung statt Mittelwert (P50/P95, CV%)

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Perf-Methodik

## Kontext

Der Harness misst pro Zelle (Maschine × Modell × Quant × Szenario × Kontext) mehrere Läufe für TTFT, Decode-tok/s und Prefill. Es stellte sich die Frage, mit welcher Kennzahl diese Wiederholungen verdichtet werden. Auf Apple-Silicon ist der Engpass der **vereinheitlichte Speicher**, nicht die reine Rechenleistung: Solange Modell + KV-Cache + Kontext in den physischen RAM passen, ist die Latenz niedrig und vorhersehbar; überschreitet man die Schwelle, beginnt der Kernel mit Kompression und Auslagerung und die Antwortzeiten werden sprunghaft. Ein Mittelwert verwischt genau dieses Verhalten — eine im Schnitt schnelle Maschine, die gelegentlich sekundenlang einfriert, ist für interaktive Nutzung schlechter als eine etwas langsamere, die nie aus dem Tritt kommt. Der Brief ist hier explizit: berichtet wird die **Verteilung, nicht der Mittelwert**.

## Entscheidung

Pro Zelle wird die **Verteilung** berichtet statt eines einzelnen Mittelwerts:

- **TTFT** als **P50 und P95** (`ttft_p50`, `ttft_p95`) — Linear-Interpolations-Perzentil (numpy-Default / „type 7").
- **Decode-tok/s und Prefill-tok/s** als **Median** (`decode_median`, `prefill_median`).
- **TTFT-Konsistenz** als **CV%** (`cv_pct` = Stdev/Mittel × 100, Sample-Stdev mit n−1) in der Spalte „Konsist. (CV%)".

Implementiert in den reinen, abhängigkeitsfreien Funktionen `percentile`, `median` und `cv_percent` in `touchstone/stats.py`; aggregiert pro Zelle in `aggregate_cells` und gerendert in `render_report_md` (`touchstone/report.py`).

## Erwogene Alternativen

- **Mittelwert (arithmetisches Mittel) als einzige Kennzahl** — verworfen, weil er das sprunghafte Verhalten bei Speicher-Überschreitung verwischt; eine im Schnitt schnelle, aber gelegentlich einfrierende Maschine würde fälschlich besser aussehen als eine durchweg konsistente. Der `mean` existiert in `stats.py` nur als Bausteinfunktion, nicht als berichtete Zellen-Kennzahl.
- **Warmup- und Cold-Start-Läufe in dieselbe Verteilung mischen** — verworfen, weil der erste Request nach Modell-Start einmalige Kosten (Gewichte mappen, Caches warm machen) zahlt und so P95 und CV% verzerren würde; daher wird der Warmup-Lauf pro Zelle aus den Aggregaten ausgeschlossen (`is_valid_for_aggregate` filtert `warmup`) und die Cold-Start-TTFT einmalig und gesondert ausgewiesen.

## Auswirkungen

- Positiv: Die Schwanz-Latenz (P95) und die Konsistenz (CV% der TTFT) werden sichtbar — genau das Verhalten, das über die interaktive Tauglichkeit entscheidet, statt es im Mittel zu glätten.
- Positiv: Robuste Verdichtung — leere Eingaben liefern `nan` (vom Renderer als `—` dargestellt) statt den Report zum Absturz zu bringen.
- Trade-off / Restgrenze: CV% braucht **mindestens 2 Samples** und einen Mittel ≠ ~0, sonst `nan`. Zudem gilt eine Validitäts-Untergrenze von `MIN_VALID_RUNS = 7` gewerteten Läufen pro Zelle (`low_n` / „⚠️ n=<k>"); darunter sind die Verteilungszahlen bewusst als unterbesetzt markiert. Aggregate schließen Warmup-, Cold-, throttled- und Akku-Läufe aus (roh in `raw.csv` erhalten).

## Belege & Links

- Spec: `docs/superpowers/specs/2026-06-19-ndeval-harness-design.md` · Code: `touchstone/stats.py`, `touchstone/report.py` · Erläuterung: `docs/explanation/design-decisions.md` („Warum Verteilung statt Mittelwert")
- Tests: `tests/test_stats.py` (Unit-Tests für `percentile`, `median`, `cv_percent`).
