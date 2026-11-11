---
paths:
  - "touchstone/client.py"
  - "touchstone/runner.py"
  - "touchstone/judge.py"
  - "touchstone/qualrun.py"
  - "touchstone/results.py"
  - "touchstone/scorecard.py"
  - "tests/test_client.py"
  - "tests/test_runner.py"
---

- **Reasoning ("thinking") models are captured, not dropped.** `client.py` reads the separate
  `delta.reasoning_content` / `delta.reasoning` field into `StreamEvent.reasoning_text`; `stream_once`
  accumulates it (separately from content, so it never triggers content-TTFT) and `run_eval` records
  its length as `EvalResponse.reasoning_chars`. A reasoning-only answer (e.g. ollama `gemma4:e4b`) has
  `content_empty=True` with a non-zero `reasoning_chars`; the monitor shows a 💭 marker per such cell.
  `stream_once` also records `reasoning_duration_s` / `reasoning_tps` (heuristic token count) and the
  first-reasoning-token time, surfaced per-answer and as a "Thinking" compare row.
- **Prefill/decode rates start at the first *generated* token, not the first content token.** For a
  reasoning model the reasoning stream begins long before the first content token, so `derive_rates`
  would otherwise shrink the decode window to ~0 and explode `decode_tps` (and starve `prefill_tps`).
  It uses `min(ttft, t_reasoning_start)` as the prefill/decode boundary. `ttft_s` itself stays "time to
  first *visible content*" — the UX latency metric, deliberately distinct from the throughput boundary.
- **Reasoning-only is scored `unscored`, not a silent 1/5.** `judge.py:score_response` splits the
  empty-content path on `reasoning_chars`: reasoning-only (content empty *and* reasoning present) →
  `unscored` (excluded from the mean, `REASONING_ONLY_RATIONALE`) — it is *our* token-starvation, not a
  verdict on the model; a genuinely empty answer (no reasoning either) still scores 1 (`EMPTY_RATIONALE`).
  `EvalResponse.reasoning_text` is persisted **only when `content_empty`** (keeps `responses.jsonl` slim)
  so the thinking stays inspectable, and `scorecard.reasoning_only_counts` surfaces the per-(model,variant)
  count in the tech-specs table.
