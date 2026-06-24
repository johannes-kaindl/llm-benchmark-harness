# Cross-Run / Machine Aggregation (`touchstone aggregate`) — Design (Ink. 4)

**Datum:** 2026-06-20
**Status:** ratifiziert (autonomer Durchlauf — Design-Entscheidungen dokumentiert, Approval-Gate per User-Anweisung „alles autonom durchziehen" übersprungen)

## Ziel
Viele `scores.csv` (je ein eval+judge-Bundle, evtl. von verschiedenen Maschinen) → **eine Hardware×Qualität-Tabelle**. Das Quality-Analogon zu `touchstone report` (das `raw.csv` über Läufe zu `report.md` aggregiert).

## Befehl
```
touchstone aggregate --runs ./runs [--out <dir>]
```
- Sammelt rekursiv `<runs>/**/scores.csv` (wie `report` mit `raw.csv`).
- Schreibt `<out|runs>/aggregate.md` (Übersicht) + `<out|runs>/scores_all.csv` (Konkatenation aller Roh-Zeilen, für eigene Auswertung).

## Datenkontrakt (Eingang = bestehende `scores.csv`)
Pro Zeile (aus `scorecard.scores_csv_rows`): `machine, chip, ram_gb, pack, pack_version, model, quant, variant, ttft_p50, decode_med, peak_ram_gb, power, metric_type, metric, weight, score`. Eine Zeile **pro (model, variant, Dimension)**; `metric_type="none"` wenn unbewertet.

## Aggregation (pure, `touchstone/aggregate.py`)
- **Gruppierung:** `(chip, ram_gb, machine, pack, pack_version, model, quant, variant)`.
- **Qualität %** je Gruppe: `Σ(score·weight) / Σ(SCALE_MAX·weight) · 100` über Dimensions-Zeilen mit nicht-leerem `score`. `SCALE_MAX=5` (Konvention der 1..5-Pack-Skala; dokumentierte Annahme, da `scores.csv` die Skala nicht trägt). Keine bewerteten Dimensionen → Qualität leer.
- **Perf** je Gruppe: `ttft_p50 / decode_med / peak_ram_gb / power` aus der ersten Zeile der Gruppe (über Dimensions-Zeilen identisch).
- **Dim-Scores:** Map `dim → score` für die Detail-Spalten (optional im md).
- **Sortierung:** nach `chip`, dann `Qualität %` absteigend (beste Hardware-Qualität-Kombi oben).

## Rendering (`render_aggregate_md`)
Tabelle: `Chip · RAM · Maschine · Modell · Quant · Variante · Pack · Qualität % · TTFT P50 (s) · Decode (tok/s) · Peak-RAM (GB) · Power`. Hinweiszeile: Skala-5-Annahme; Sicherheit/K.-o. bleibt pro-Bundle in `scorecard.md` (nicht in `scores.csv`, daher hier nicht rekonstruierbar).

## Bewusst außerhalb v1
Sicherheits-/K.-o.-Spalte im Aggregat (braucht `ko_rule` + Red-Flags, nicht in `scores.csv`) · per-Dimension-Pivot-Spalten über mehrere Packs (bleibt in `scores_all.csv`) · Trend über Zeit.

## Module / Dateien
- **neu:** `touchstone/aggregate.py` (`load_scores_csv`, `load_all_scores`, `AggRow`, `aggregate`, `render_aggregate_md`, `write_scores_all_csv`), `tests/test_aggregate.py`
- **geändert:** `touchstone/cli.py` (`aggregate`-Befehl), `AGENTS.md` (cli-Zeile + Modul)
- **unverändert:** keine neuen Deps (stdlib `csv`).
