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

## Features

- **Verteilung statt Mittelwert** — TTFT als P50/P95, Decode/Prefill als Median, Konsistenz als
  CV%. Auf Apple Silicon springt die Latenz an der Speicher-Schwelle; ein Mittelwert verwischt
  genau das.
- **Entkoppelter Host-Sampler** — Speicherdruck, Swap, Power-Source und Throttling schreibt ein
  *eigener Prozess* mit, nachträglich per Zeitstempel gemergt. Nie aus dem Request-Thread
  geschätzt.
- **Antwort-Qualität, nicht nur Tempo** — ein Use-Case-**Pack** (`packs/*.yaml`: Prompts,
  Green-/Red-Flags, gewichtete Dimensionen, Sicherheits-K.-o.) läuft deterministisch; ein
  austauschbarer LLM-as-judge macht daraus eine gewichtete Scorecard. Ein neuer Einsatzzweck ist
  ein neues YAML, kein neuer Code.
- **System-Prompts als gemessene Achse** — jeder Prompt läuft einmal pro System-Prompt-Variante.
  Derselbe Lauf zeigt damit, ob dein Prompt überhaupt etwas bringt.
- **Engine-agnostisch by construction** — die einzige engine-bewusste Datei ist `client.py`.
  LM Studio, mlx-lm, Ollama und llama.cpp sind ein Config-Tausch, nie ein Code-Zweig.
- **Überall fortsetzbar** — `eval`, `judge` und die Nacht-`queue` machen nach einem Abbruch dort
  weiter, wo sie standen.
- **Vergleich über Maschinen** — `aggregate` rollt viele Bundles in eine
  Hardware×Qualität-Tabelle und berichtet das Modell-Delta (Peak minus Baseline), das als
  einziges über Maschinen hinweg trägt.
- **Nacht-Queue** — mehrere Modelle sequenziell, jedes mit frischer RAM-Baseline
  (entladen → settle → eval → judge), mit Watchdog pro Schritt.
- **Optionale Web-Steuerzentrale** — konfigurieren, starten/stoppen, live zusehen, vergleichen und
  exportieren im Browser, als out-of-process Control-Plane, die nie im eigenen Prozess misst.

## Voraussetzungen

- **macOS.** Der Mess-Kern ist portabel, der Host-Sampler liest aber `memory_pressure`, `pmset`
  und `powermetrics`.
- **Python 3.12+** über [`uv`](https://docs.astral.sh/uv/) (`brew install uv`).
- **Ein lokaler OpenAI-kompatibler Endpoint** — LM Studio, `mlx_lm.server`, `mlx-openai-server`,
  Ollama oder llama.cpp. Noch keiner da? Siehe
  [uplink.jkaindl.de/llm-setup](https://uplink.jkaindl.de/llm-setup).
- **Optional — passwortloses sudo für `powermetrics`.** Ohne bleibt der Throttle-Flag aus und
  throttled Läufe werden *nicht* ausgeschlossen (siehe
  [How-to: neue Maschine einrichten](docs/how-to/neue-maschine-einrichten.md)).
- **Optional — das `[gui]`-Extra** (FastAPI/uvicorn/Jinja) für die Web-Steuerzentrale.

## Installation

```bash
git clone https://codeberg.org/jkaindl/llm-benchmark-harness
cd llm-benchmark-harness
uv sync
uv sync --extra gui     # optional: Web-Steuerzentrale
```

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
Ein neuer Use-Case ist ein neues YAML, kein neuer Code. Mitgeliefert sind zwei Packs:
`ndassist` (Neurodivergenz-Assistent) und `buero` (Büro-/Wissensarbeit-Assistent).

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

Einstieg: [`docs/README.md`](docs/README.md).

- [Tutorial](docs/tutorial.md) — einmal komplett: installieren → messen → bewerten → Scorecard lesen
- [How-to-Guides](docs/how-to/) — [eigenen Pack bauen](docs/how-to/eigenen-pack-bauen.md) ·
  [neue Maschine einrichten](docs/how-to/neue-maschine-einrichten.md) ·
  [Nacht-Queue fahren](docs/how-to/nacht-queue-fahren.md) ·
  [Web-Steuerzentrale nutzen](docs/how-to/gui-nutzen.md)
- [Reference](docs/reference/metrics-and-schema.md) — Metrik-Definitionen, CSV-Schema, Config-Schlüssel
- [Explanation](docs/explanation/design-decisions.md) — warum Verteilung statt Mittelwert, der Zwei-Prozess-Split, warum die GUI out-of-process ist
- [Decisions](docs/decisions/README.md) — 15 ADRs: Kontext, Alternativen, Auswirkungen

## Lizenz

Code: **AGPL-3.0** — siehe [`LICENSE`](LICENSE); kommerzielle Dual-License-Option in [`LICENSING.md`](LICENSING.md).
Dokumentation/Text: **CC BY-SA 4.0** — siehe [`LICENSE-DOCS`](LICENSE-DOCS).
