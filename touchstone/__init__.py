"""touchstone — thin benchmark harness for local OpenAI-compatible LLM endpoints.

Measures TTFT / prefill-tok/s / decode-tok/s with distribution and correlates
each run with macOS memory pressure and thermal throttling. Engine-agnostic:
the only thing that changes between an M1 (LM Studio) and an M5 (mlx-lm) run is
the config file.
"""

__version__ = "0.2.0"
