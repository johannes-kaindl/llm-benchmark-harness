# llm-ramcheck

> [🇬🇧 English](README.md) · 🇩🇪 Deutsch

Ein dünnes CLI, das ein lokales LLM über einen **OpenAI-kompatiblen Endpoint** benchmarkt —
es erfasst TTFT, Prefill-tok/s und Decode-tok/s **mit Verteilung**, während ein
**entkoppelter Host-Sampler** macOS-Speicherdruck und Throttling mitschreibt, und mergt
beides zu einer Markdown-Tabelle (+ CSV). Gleicher Code auf M1 (LM Studio) und M5 (mlx-lm) —
nur die Config wird getauscht.

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Docs: CC BY-SA 4.0](https://img.shields.io/badge/docs-CC%20BY--SA%204.0-lightgrey.svg)](LICENSE-DOCS)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)

## Schnellstart

```bash
uv sync
# M1 → LM Studio (http://localhost:1234/v1):
uv run ramcheck run    --config config.m1.yaml
# M5 → mlx_lm.server (:8080) / mlx-openai-server (:8000):
uv run ramcheck run    --config config.m5.yaml
# Embedding-Durchsatz (separat):
uv run ramcheck embed  --config config.m5.yaml
# report.md aus gesammelten raw.csv (neu) erzeugen:
uv run ramcheck report --runs ./runs
```

## Nutzung

`endpoint.base_url` auf den lokalen Server zeigen lassen, `machine`/`models` setzen, laufen
lassen. Jeder Lauf fährt eine Szenario-×-Kontext-Matrix, verwirft pro Zelle einen Warmup,
misst eine separate Cold-Start-TTFT und schreibt ein `report.md` (dessen Spalten direkt in
die Vergleichsnotiz passen) plus eine `raw.csv` mit einer Zeile pro Request.

Zwei Prozesse bleiben **bewusst entkoppelt**: der **Latenz-Runner** (Requests) und der
**Host-Sampler** (`psutil` + `powermetrics` + `memory_pressure` + `pmset`), nachträglich per
Zeitstempel gemergt. Speicher wird nie aus dem Request-Thread geschätzt.

Berichtet wird **die Verteilung, nicht der Mittelwert**: TTFT als P50/P95, Decode/Prefill als
Median, TTFT-Konsistenz als CV%. Throttled- und Akku-Läufe werden geflaggt und aus den
Aggregaten ausgeschlossen (roh in der CSV erhalten).

> **macOS-Hinweis:** Die Throttle-Erkennung ruft `sudo powermetrics` auf. Erlaube
> passwortloses sudo dafür, sonst bleibt der Throttle-Flag aus (Läufe werden dann *nicht*
> als throttled ausgeschlossen). Siehe [AGENTS.md](AGENTS.md) → Gotchas.

## Dokumentation

- [Reference](docs/reference/) — Metrik-Definitionen, CSV-Schema, Config-Schlüssel
- [Explanation](docs/explanation/) — warum Verteilung statt Mittelwert, der Zwei-Prozess-Split

## Lizenz

Code: **AGPL-3.0** — siehe [`LICENSE`](LICENSE); kommerzielle Dual-License-Option in [`LICENSING.md`](LICENSING.md).
Dokumentation/Text: **CC BY-SA 4.0** — siehe [`LICENSE-DOCS`](LICENSE-DOCS).
