# ADR-0004: Modell-Delta vs System-Peak als vergleichbare Speicherzahl

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Perf-Methodik

## Kontext
Der Harness braucht eine Speicherzahl, die eine Modell-Last **zwischen Maschinen** vergleichbar macht. Der rohe Speicher-Peak `sys_used_mb` (im UI „System-Peak") misst den gesamten belegten Systemspeicher — Betriebssystem, Browser, alles. Er sagt etwas über die *momentane* Maschine aus, ist aber zwischen Maschinen nicht vergleichbar: dieselbe Modell-Last sieht auf einem frisch gebooteten Laptop anders aus als auf einem mit 40 offenen Tabs (`design-decisions.md`). Erschwerend kommt die Apple-Silicon-Eigenheit hinzu: Im *Unified Memory* liegen Gewicht, KV-Cache, Kontext und Aktivierungen zusammen; der Server-PID-RSS unterzählt zudem die mmap'ten Modellgewichte (`report.py`, Kommentare zu `peak_rss_mb`). Der Sampler-Tick ist die engine-agnostische Quelle der Wahrheit für Speicher (`models.py`, `ResourceSample`-Docstring).

## Entscheidung
Als maschinen-vergleichbare Zahl wird das **Modell-Delta** = `Peak − Baseline` berichtet, im Feld `sys_used_delta_mb` (im UI „Modell-Delta", in `scores.csv` als `model_delta_gb`). Die **Baseline** ist der Systemspeicher unmittelbar vor der ersten Anfrage: ein vor der Mess-Schleife genommener Tick, der als erste `resources.jsonl`-Zeile mit `baseline: true` geschrieben wird (`models.py` `ResourceSample.baseline`; `design-decisions.md`). Weil dieser Tick außerhalb jedes Lauf-Fensters liegt, leitet `merge.resources_for_window` die Baseline aus der *vollständigen* Sample-Liste ab (`baseline_sys_used_mb`, `merge.py:42–51`) und reicht sie an `aggregate_window` durch, wo `delta = peak_used − baseline_mb` gebildet wird (`merge.py:83–84`). Von dort fließt sie über `merge_run` in den `RunRecord` (`merge.py:115`). Der rohe System-Peak `sys_used_mb` bleibt parallel sichtbar.

## Erwogene Alternativen
- **Nackter System-Peak (`sys_used_mb`) als vergleichbare Zahl** — verworfen, weil er den gesamten Host-Speicher (OS, Browser, sonstige Last) enthält und so zwischen Maschinen nicht vergleichbar ist; er bleibt nur als „passt es überhaupt auf diese Maschine?"-Signal stehen (`design-decisions.md`).
- **Server-PID-RSS (`peak_rss_mb`) als RAM-Wahrheit** — verworfen, weil RSS auf Apple Silicon die mmap'ten Modellgewichte unterzählt; daher nur als Hinweis in Klammern neben dem System-Wert geführt (`report.py`, `CellAggregate.peak_rss_mb`-Kommentar und Render-Hinweistext).
- **Per-Prompt-KV-Isolation (eigenes Speicher-Fenster je Anfrage)** — bewusst aufgeschoben, weil sie deterministisches Entladen zwischen den Prompts erfordert, das ein fremder, dauerlaufender Endpoint nicht garantiert (`design-decisions.md`).
- **Dekomposition Gewicht vs. KV/Kontext** — verworfen, weil sich diese Anteile im Unified Memory nicht sauber trennen lassen; das Delta wird daher nicht weiter zerlegt (`design-decisions.md`).

## Auswirkungen
- Positiv: Eine maschinen-vergleichbare Speicherzahl (Modell-Delta), die den variablen Host-Grundverbrauch herausrechnet, weil die Baseline außerhalb jedes Lauf-Fensters aus der vollständigen Sample-Liste abgeleitet wird.
- Positiv: Robust gegen ältere Bundles ohne Marker — fehlt der `baseline`-Flag, fällt `baseline_sys_used_mb` auf das `min` aller Samples zurück (`merge.py:48–51`); `sys_used_delta_mb` ist `None`, nicht falsch, wenn keine Baseline existiert (`merge.py:84`).
- Trade-off / Restgrenze: Bewusste Unschärfe je nach Ladezeitpunkt — ist das Modell beim Sampler-Start schon geladen, misst das Delta die Inferenz-Last (KV-Cache, Kontext, Aktivierungen); lädt der Server faul nach, steckt auch das Gewicht im Delta (`design-decisions.md`).
- Trade-off / Restgrenze: KV-/Kontext-Anteil wird nicht vom Gewicht getrennt (Unified Memory); Per-Prompt-KV-Isolation als nächste Stufe ist aufgeschoben. System-Peak bleibt daneben sichtbar als Fit-auf-die-Maschine-Signal (`design-decisions.md`).

## Belege & Links
- Rationale: `docs/explanation/design-decisions.md` · Code: `touchstone/merge.py` (`baseline_sys_used_mb`, `aggregate_window`, `resources_for_window`, `merge_run`), `touchstone/models.py` (`ResourceSample.baseline`, `ResourceAggregate.sys_used_baseline_mb`/`sys_used_delta_mb`, `RunRecord.sys_used_delta_mb`, `RAW_CSV_COLUMNS`), `touchstone/report.py` (`CellAggregate.peak_sys_used_mb`/`peak_rss_mb`, Render-Hinweistext) · Tests: nicht im Quellen-Satz dieses ADR enthalten
- Verwandt: keine offensichtlich
