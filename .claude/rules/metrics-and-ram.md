---
paths:
  - "touchstone/sampler.py"
  - "touchstone/merge.py"
  - "touchstone/report.py"
  - "touchstone/models.py"
  - "touchstone/runner.py"
  - "touchstone/hostinfo.py"
  - "docs/reference/metrics-and-schema.md"
  - "tests/test_schema_doc.py"
  - "tests/test_report.py"
  - "tests/test_merge.py"
  - "tests/test_runner.py"
---

- **Peak-RAM = system memory, not RSS.** On Apple Silicon mlx mmaps the weights into unified
  memory, so the server-PID RSS undercounts the model (~0.2 GB vs ~18 GB). The report leads with
  peak *system* memory + `memory_pressure`; RSS is only a parenthetical hint.
- **System-Peak vs Modell-Delta — the cross-machine number is the delta.** The raw `sys_used_mb`
  peak ("System-Peak") includes the whole OS + every other process, so it is **not** comparable
  across machines. We report `sys_used_delta_mb = peak − baseline` ("Modell-Delta") as the
  comparable figure. **Baseline** = the `baseline=True` resources.jsonl tick captured at
  `sampler.run_to_file` *before the first request* (or, on an older bundle without the marker,
  `min` of all samples). The harness drives an *already-running* endpoint: on a pre-loaded server
  the delta is the inference-time growth (KV/context/activations); on a lazy-loading server it
  also includes the weights. The delta lives in unified memory where the context/KV share is **not**
  cleanly separable from the weights (documented imprecision); per-prompt KV isolation is out of
  scope. `merge.resources_for_window` derives the baseline from the *full* sample list (the baseline
  tick is outside every run window) and threads it into `aggregate_window`; `RunRecord` /
  `EvalResponse` / `scores.csv` (`model_delta_gb`) all carry it. **When you add a RAM field, keep
  the `RAW_CSV_COLUMNS ↔ RunRecord` import-time assertion green** (`sys_used_delta_mb` sits right
  after `sys_used_mb` in both).
- **Cold-start** is the very first request of a whole run (flagged `is_cold_start`), reported
  on its own line, never in the aggregates.
- **Die TTFT-Aggregate messen auf einer Engine mit Prefix-Cache den Cache, nicht die Latenz — maßgeblich ist die Spalte „TTFT kalt".** Der Latenz-Runner schickt je Zelle `runs_per_cell`-mal **denselben** Prompt; ab dem zweiten entfällt auf LM Studio, vLLM und Splash fast der ganze Prefill (Splash: P50 0,21 s gegen 7,3 s kalt). Die ehrliche Latenz ist der **erste** Request der Zelle — Cold-Start für die erste Zelle, sonst der ohnehin verworfene Warmup; `report._first_requests` leitet ihn aus den vorhandenen Flags ab (bewusst kein eigenes Feld, das dieselbe Wahrheit doppelt hielte). In der ersten Zelle trägt er zusätzlich das Laden des Modells und ist mit ⁽ᴸ⁾ markiert — gefunden erst an echten Daten (`mlx_lm.server`, cacht nicht: kalt 6,42 s gegen 0,32 s in Zelle 0, 3,85 gegen 4,17 s in Zelle 1). `cached_tokens` (aus `usage.prompt_tokens_details`, in `RunRecord`/`raw.csv` direkt hinter `actual_prompt_tokens`) belegt Cache-Treffer, statt sie zu vermuten; `None` heißt „Engine meldet es nicht" und ist **nicht** 0 — `mlx_lm.server` und Ollama melden es nicht, der Report zeigt dann `—`. Alte Bundles (alles bisher gegen `mlx_lm.server`/Ollama) sind nachweislich nicht betroffen: kalt ≈ warm ab Zelle 1. Nebenbei behoben: `report.load_raw_csv` erkannte optionale Zahlen an einer handgepflegten Namensliste, der `sys_used_delta_mb` fehlte — das Modell-Delta kam als **String** zurück; jetzt entscheidet der deklarierte Typ. `tests/test_schema_doc.py` hält die Spaltenliste in `docs/reference/metrics-and-schema.md` gegen `RAW_CSV_COLUMNS` (sie war schon einmal um `sys_used_delta_mb` hinterher).
