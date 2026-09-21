# Reference — Metriken, CSV-Schema, Config

## Metrik-Ableitung

| Metrik | Definition |
|---|---|
| **TTFT** | Zeit vom Request-Absenden bis zum ersten nicht-leeren Streaming-Chunk. |
| **Prefill-tok/s** | `prompt_tokens / TTFT`. |
| **Decode-tok/s** | `completion_tokens / (e2e − TTFT)`. |
| **e2e** | Zeit vom Absenden bis zum letzten Chunk. |

`prompt_tokens` / `completion_tokens` kommen aus dem `usage`-Feld (Streaming mit
`stream_options.include_usage = true`). Fehlt `usage`, wird `completion_tokens`
heuristisch aus dem akkumulierten Text geschätzt.

## Aggregation pro Zelle

Eine **Zelle** = `(machine, model, quant, scenario, target_ctx)`. Pro Zelle:

- **TTFT** → P50 und P95 (lineare Interpolation, „type 7").
- **Decode / Prefill** → Median.
- **Konsistenz** → CV% der TTFT = `Stdev / Mittel × 100` (Stichproben-Stdev, n−1).
- **Kontext (ist)** → Median der echten `prompt_tokens` (nicht der Zielwert).
- **Peak-RAM / Druck / Swap** → Maxima über das Lauffenster aus dem Host-Sampler.
- **TTFT kalt** → der **erste** Request der Zelle (für die erste Zelle des Laufs der Cold-Start, sonst der Warmup). Die gewerteten Läufe wiederholen denselben Prompt; auf einer Engine mit Prefix-Cache (LM Studio, vLLM, Splash) messen ihre Aggregate deshalb den Cache, und nur dieser eine Wert ist die ehrliche Latenz. In der ersten Zelle enthält er zusätzlich das Laden des Modells und ist mit ⁽ᴸ⁾ markiert — gemessen an `mlx_lm.server`, das gar nicht cacht: kalt 6,42 s gegen 0,32 s warm in Zelle 0, aber 3,85 gegen 4,17 s in Zelle 1.
- **Cache warm/kalt** → Anteil des Prompts, den die Engine in `usage.prompt_tokens_details.cached_tokens` als gecacht meldet (Median der gewerteten Läufe bzw. der Kalt-Request). `—` heißt: die Engine meldet es nicht — unbekannt, nicht 0 %. `mlx_lm.server` und Ollama melden es nicht.

**Ausgeschlossen** aus den Aggregaten (aber roh in `raw.csv`): Warmup (erster Lauf jeder
Zelle), Cold-Start, fehlgeschlagene, `throttled`- und `power=battery`-Läufe. Die Spalte
*Throttle/Akku* zählt, wie viele ausgeschlossen wurden.

## `raw.csv` — Spalten (eine Zeile pro Request)

```
run_id, machine, model, quant, engine, engine_version, scenario, target_ctx,
actual_prompt_tokens, cached_tokens, completion_tokens, ttft_s, decode_tps, prefill_tps,
e2e_s, peak_rss_mb, sys_used_mb, sys_used_delta_mb, swap_delta_mb, mem_pressure_max,
throttled, power_source, seed, warmup, is_cold_start, ok, error
```

Quelle der Wahrheit: `touchstone/models.py::RAW_CSV_COLUMNS` (wird beim Import gegen
`RunRecord` geprüft).

## `report.md` — Spalten (SSOT-Tabelle)

`Datum · Maschine · Modell · Quant · Kontext (ist) · TTFT P50/P95 (s) · TTFT kalt (s) · Decode (tok/s) · Prefill (tok/s) · Cache warm/kalt · Peak-RAM / Druck / Swap · Qual. · Flow · Konsist. (CV%) · Throttle/Akku`

`Qual.` und `Flow` bleiben leer (manuelle Bewertung). Cold-Start steht in einem eigenen Block.

## Config-Schlüssel

| Schlüssel | Bedeutung |
|---|---|
| `endpoint.base_url` / `endpoint.api_key` / `endpoint.api_key_file` | OpenAI-kompatibler Endpoint; Key `"not-needed"` (lokal). Echter Key eines Remote-Endpoints per `api_key_file` (Pfad, `~` erlaubt) — gesetzt gewinnt die Datei; gilt auch im `judge.yaml`. |
| `machine` | Freier Bezeichner für den Report-Header. |
| `runs_per_cell` | N pro Zelle (≥2; 1 Warmup wird verworfen). |
| `seed`, `temperature` | Reproduzierbarkeit (`temperature: 0.0`). |
| `context_buckets` | Ziel-Tokenzahlen für `rag_synth` / `longctx_stress` (z. B. 4096/16384/32768). |
| `scenarios` | Untermenge von `bodydouble, compose, rag_synth, longctx_stress, vlm`. |
| `models[]` | `{id, quant, max_tokens_default}`. |
| `max_tokens` | Optionale Per-Szenario-Overrides. |
| `server_process_match` | Substring zum Finden des Server-Prozesses (Peak-RSS). |
| `power_check` | `pmset` (Power-Source) + `powermetrics` (Throttle, braucht sudo). |
| `engine` / `engine_version` | Optionaler Override für den Report-Header. |
| `vlm.image_path` | Bild für das `vlm`-Szenario. |
| `embed.{model,num_chunks,chunk_tokens}` | Parameter für `touchstone embed`. |
