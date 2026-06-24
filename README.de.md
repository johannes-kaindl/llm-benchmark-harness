# llm-touchstone

> [🇬🇧 English](README.md) · 🇩🇪 Deutsch

Ein dünnes CLI, das ein lokales LLM über einen **OpenAI-kompatiblen Endpoint** benchmarkt —
es erfasst TTFT, Prefill-tok/s und Decode-tok/s **mit Verteilung**, während ein
**entkoppelter Host-Sampler** macOS-Speicherdruck und Throttling mitschreibt, und mergt
beides zu einer Markdown-Tabelle (+ CSV). Es bewertet außerdem die **Antwort-Qualität**
(deterministische Erfassung + optionaler LLM-as-judge) und steuert den ganzen Ablauf wahlweise
über eine lokale **Web-Steuerzentrale**. Gleicher Code auf M1 (LM Studio) und M5 (mlx-lm) —
nur die Config wird getauscht.

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Docs: CC BY-SA 4.0](https://img.shields.io/badge/docs-CC%20BY--SA%204.0-lightgrey.svg)](LICENSE-DOCS)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)

## Schnellstart

```bash
uv sync
# Latenz-Benchmark — M1 → LM Studio (:1234), M5 → mlx_lm.server (:8080):
uv run touchstone run    --config config.m1.yaml
uv run touchstone embed  --config config.m5.yaml   # Embedding-Durchsatz (separat)
uv run touchstone report --runs ./runs             # report.md aus raw.csv (neu) erzeugen

# Qualitäts-Eval — ein Use-Case-Pack laufen lassen, dann bewerten:
uv run touchstone eval   --pack packs/ndassist.yaml --config config.m5.yaml
uv run touchstone judge  --bundle runs/<ts>_eval_ndassist --judge-config judge.yaml
uv run touchstone aggregate --runs ./runs          # Cross-Machine Hardware×Qualität-Tabelle

# Web-Steuerzentrale (optionales [gui]-Extra):
uv sync --extra gui
uv run touchstone gui                              # konfigurieren → starten → zusehen → auswerten → vergleichen → exportieren
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

**Qualitäts-Eval (die zweite Hälfte).** Perf sagt nicht, ob ein Modell *gut antwortet*. `eval`
fährt deterministisch ein Use-Case-**Pack** (`packs/*.yaml` — Prompts + Green/Red-Flags +
gewichtete Dimensionen + Sicherheits-K.-o. + System-Prompt-Varianten) auf der Testmaschine und
erfasst Antworten + Tech-Specs; `judge` bewertet sie dann mit einem pluggable LLM-as-judge zu
einer gewichteten Scorecard (`aggregate` rollt viele Bundles in eine Hardware×Qualität-Tabelle).
Generierung und Bewertung sind **zwei entkoppelte Phasen**, beide inkrementell und fortsetzbar.
Ein neuer Use-Case ist ein neues YAML, kein neuer Code.

**Web-Steuerzentrale (`touchstone gui`).** Ein optionaler lokaler FastAPI-Server (das `[gui]`-Extra
— build-freies HTMX/Alpine, vom Mess-Kern isoliert) bringt den ganzen Ablauf in den Browser:
sehen, was ein Pack nach welchen Kriterien testet, Läufe konfigurieren und **starten/stoppen**,
live zusehen, Ergebnisse ansehen, über Maschinen vergleichen und exportieren. Es ist ein
**out-of-process Control-Plane** — es spawnt dieselben `touchstone eval/judge`-Subprozesse wie die
CLI, sodass der Mess-Loop entkoppelt bleibt und `runs/` die Single Source of Truth bleibt. An
127.0.0.1 gebunden, nur ein Mess-Lauf gleichzeitig.

> **macOS-Hinweis:** Die Throttle-Erkennung ruft `sudo powermetrics` auf. Erlaube
> passwortloses sudo dafür, sonst bleibt der Throttle-Flag aus (Läufe werden dann *nicht*
> als throttled ausgeschlossen). Siehe [AGENTS.md](AGENTS.md) → Gotchas.

## Dokumentation

- [Reference](docs/reference/) — Metrik-Definitionen, CSV-Schema, Config-Schlüssel
- [Explanation](docs/explanation/) — warum Verteilung statt Mittelwert, der Zwei-Prozess-Split, warum die GUI out-of-process ist

## Lizenz

Code: **AGPL-3.0** — siehe [`LICENSE`](LICENSE); kommerzielle Dual-License-Option in [`LICENSING.md`](LICENSING.md).
Dokumentation/Text: **CC BY-SA 4.0** — siehe [`LICENSE-DOCS`](LICENSE-DOCS).
