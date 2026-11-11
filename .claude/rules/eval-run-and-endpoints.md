---
paths:
  - "touchstone/preflight.py"
  - "touchstone/qualrun.py"
  - "touchstone/config.py"
  - "touchstone/client.py"
  - "touchstone/cli.py"
  - "touchstone/pack.py"
  - "config.example.yaml"
  - "config.m1.yaml"
  - "config.m5.yaml"
  - "config.m5-lmstudio.yaml"
  - "tests/test_preflight.py"
  - "tests/test_reasoning_effort.py"
  - "tests/test_config.py"
---

- **Pre-flight smoke runs before the matrix.** `preflight.preflight_models` sends one small request per
  model with that model's *effective* budget (`max(pack prompt max_tokens) + ModelSpec.reasoning_headroom_tokens`)
  after `iter_eval_cells` but **before** `sampler.start()` (never inside the measured window) and classifies
  `ok | reasoning_only | empty | error`. It **never raises** and never measures. Default **warns** (CLI print
  + a `preflight` event in `events.jsonl` → folded by `events.build_view` → GUI live banner / webmon banner);
  `eval --strict-preflight` raises before the matrix (no run-dir garbage). `resume` skips it.
- **Eval lets models answer freely by default.** `PackPrompt.max_tokens` defaults to `None` (no cap) for the
  qualitative eval — closer to real use, and a reasoning model isn't starved before it reaches visible content.
  `client.stream` omits `max_tokens` from the API call when `None` (the server decides, context-window-bounded);
  a positive int caps it (set per pack prompt for a controlled budget). The **latency runner** (`run`) keeps its
  fixed `config.max_tokens_for` budget — it measures decode under a comparable load.
- **Thinking knobs on `ModelSpec` are opt-in and default-neutral.** `reasoning_headroom_tokens` is a fine-tuning
  knob: it adds extra *total* budget for THAT model **on top of an explicit `pack.prompt.max_tokens` cap** (the
  visible answer budget stays the cap → fair compare); with the default free budget there's no cap to add to, so
  it's ignored. A small cap is a legitimate test setup, so the harness never inflates it silently. `extra_body`
  is forwarded verbatim to `chat.completions.create` (e.g. `{"chat_template_kwargs": {"enable_thinking": false}}`)
  — engine-agnostic, no engine branch outside `client.py`, forwarded only when non-empty. The judge backend
  never disables thinking.
- **Reasoning-Effort als `extra_body`, oberste Body-Ebene.** `ModelSpec.extra_body = {"reasoning_effort": "medium"}`
  landet über das OpenAI-SDK **auf oberster Ebene** im Request (wie opencodes `options.reasoningEffort`);
  LM Studio liest es dort, `chat_template_kwargs` reicht es **nicht** durch. Das qwen3.8-Template kennt nur
  `xhigh` (Default) / `medium` / `low` und wirft sonst einen Template-Fehler → `tools` hat einen **Pre-Flight**
  mit exakt diesem `extra_body` und bricht vor der Matrix ab (Eval: `--strict-preflight`). Das Label (`quant`)
  muss den Effort tragen (`4bit-medium`), sonst kollidieren gleich quantisierte Bundles im Vergleich; das
  Manifest hält `extra_body`/`reasoning_effort` fest (`tests/test_reasoning_effort.py`, echte CLI gegen Stand-in).
- **Remote-Endpoint (z. B. Hetzner Inference) = nur Qualität.** Key per `endpoint.api_key_file` (nie ins YAML; eine solche Config nicht als `config*.yaml` ins Repo-Root legen — `tests/test_config.py` validiert jede ausgelieferte Config und scheiterte ohne Token-Datei). TTFT/tok/s/RAM eines Remote-Bundles sind ohne Aussage (der Sampler misst den lokalen Mac). Hetzner (vLLM hinter Gateway „HeRay"): Reasoning in `delta.reasoning`, `reasoning_effort` oben im Body wirkt (`none` = Aus; `max` gibt es nur dort, lokal 400 → nicht in Vergleichsmatrizen), SSE-Zeilen `data:{…}` ohne Leerzeichen (SDK ok, Handparser nicht), `prompt + max_tokens ≤ 262144` sonst 400.
- **Eval-Resume nimmt die Modelle aus dem Bundle**, nicht aus der Config: vorher lief ein mit `--models-json`
  gestartetes Bundle beim `--resume` mit den `models:` der Config weiter (still ein anderes Modell).
