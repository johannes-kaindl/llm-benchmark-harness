---
paths:
  - "touchstone/webmon.py"
  - "touchstone/events.py"
  - "touchstone/judge_events.py"
  - "touchstone/tail.py"
  - "touchstone/loadview.py"
  - "tests/test_webmon.py"
  - "tests/test_events.py"
  - "tests/test_judge_events.py"
  - "tests/test_tail.py"
  - "tests/test_loadview.py"
---

- **The live monitor never tails `responses.jsonl`.** It is rewritten wholesale at finalize
  (`qualrun._write_responses`). Live progress comes from the append-only `events.jsonl`
  (fed by `run_eval`'s `on_cell_*` callbacks); live host-load from `resources.jsonl`.
- **The judge monitor (`judge --web`) never tails `resources.jsonl`.** The judge runs on a
  *different* endpoint (a cloud/local judge), so the bundle's `resources.jsonl` (from the
  earlier `eval` run) is stale and irrelevant. The judge view (`touchstone/judge_events.py`,
  `TAILS_RESOURCES=False`) shows score distribution / red-flags / a master-scorecard preview
  instead of a load panel. The master `%`/safety values are computed in the host process and
  shipped pre-rendered (the monitor never sees the `Pack`). Like `eval --web`, the judge path
  is byte-identical without `--web` (additive callbacks, default off) and `_hold_monitor`
  keeps the dashboard up until Ctrl-C.
