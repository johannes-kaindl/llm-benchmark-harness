# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.

Das Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
dieses Projekt folgt [Semantic Versioning](https://semver.org/lang/de/) (Tags **ohne** v-Präfix).

## [Unreleased]

### Added
- `vlm`-Szenario produktiv gegen einen echten MLX-VLM-Server gegengetestet (bislang nur Platzhalter-Bild).

## [0.1.0] — 2026-06-15

### Added
- Erstes Gerüst des Benchmark-Harness `llm-ramcheck`.
- Latenz-Runner über OpenAI-kompatibles Streaming: TTFT, Prefill-tok/s, Decode-tok/s
  aus dem `usage`-Feld; ein Warmup pro Zelle verworfen, Cold-Start separat ausgewiesen.
- Entkoppelter Host-Sampler (eigener Prozess): `psutil` + `powermetrics` (Throttle) +
  `memory_pressure` + `pmset`; Merge per Zeitfenster/`run_id`.
- Verteilungs-Statistik: TTFT P50/P95, Decode/Prefill als Median, Konsistenz als CV%.
- Szenarien `bodydouble`, `compose`, `rag_synth`, `longctx_stress`, `vlm`
  mit deterministischer Token-Auffüllung auf 4K/16K/32K (Bericht gegen echte `prompt_tokens`).
- `report.md` (SSOT-Spalten) + `raw.csv`; Throttle-/Akku-Läufe geflaggt und aus Aggregaten ausgeschlossen.
- Sub-Command `embed` für Embedding-Durchsatz (Embeddings/s, Gesamtzeit, Peak-RAM).
- Configs `config.example.yaml`, `config.m1.yaml`, `config.m5.yaml` — Maschinenwechsel nur per Config.

### Hardening (aus adversarialer Spec-Review)
- Validitätsgrenze sichtbar gemacht: Zellen mit < 7 gewerteten Läufen werden im Report mit `⚠️ n=<k>`
  markiert (fängt auch zu viele throttled/Akku-Ausschlüsse ab); CLI warnt bei `runs_per_cell < 8`.
- `context_buckets` wird validiert (nicht leer, alle Werte positiv).
- pre-commit-mypy-Hook auf `ramcheck/` korrigiert (zeigte auf nicht-existentes `src/`); stale pytest-`--ignore` entfernt.
