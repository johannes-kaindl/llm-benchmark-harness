# prompts/

Base texts for the scenarios. German, deterministic.

- **`base_de.md`** — padding source for `rag_synth` / `longctx_stress`. Repeated
  and trimmed to hit the target token bucket (4K/16K/32K). Editing it changes the
  filler corpus; the harness reports against the *actual* `prompt_tokens` from
  `usage`, so exact wording doesn't bias the numbers.
- **`vlm_sample.png`** — image for the `vlm` scenario (M5 only). The shipped file
  is a tiny placeholder; replace it with a real screenshot or PDF page to exercise
  the multimodal path under realistic load. Path is set in `config.m5.yaml` → `vlm.image_path`.

The `bodydouble` and `compose` prompt texts live in `ramcheck/prompts.py` (they are
short and fixed, so embedding them keeps the scenarios reproducible without file I/O).
