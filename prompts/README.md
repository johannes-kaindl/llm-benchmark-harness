# prompts/

Base texts for the scenarios. German, deterministic.

- **`base_de.md`** — padding source for `rag_synth` / `longctx_stress`. Repeated
  and trimmed to hit the target token bucket (4K/16K/32K). Editing it changes the
  filler corpus; the harness reports against the *actual* `prompt_tokens` from
  `usage`, so exact wording doesn't bias the numbers.
- **`vlm_sample.png`** — image for the `vlm` scenario (M5 only). A 900×620 German
  "document page" with real rendered text, so "extract text / summarize" is a meaningful
  task. Swap in your own screenshot/PDF page for representative load; path is set in
  `config.m5.yaml` → `vlm.image_path`. Note: the `vlm` scenario needs a **vision-capable**
  model loaded on the endpoint (a text-only model will reject the image content).

The `bodydouble` and `compose` prompt texts live in `touchstone/prompts.py` (they are
short and fixed, so embedding them keeps the scenarios reproducible without file I/O).
