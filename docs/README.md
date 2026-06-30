# touchstone — Doku

**touchstone** (`llm-touchstone`) ist ein **dünner** Benchmark-Harness für *lokale* LLM-Endpoints. Er
fährt ein festes Prompt-Set über einen **OpenAI-kompatiblen** Endpoint und misst zwei Hälften:

- **Performance** — TTFT / Prefill-tok/s / Decode-tok/s **mit Verteilung** (P50/P95, CV%), in einem
  *entkoppelten* Prozess korreliert mit macOS-Speicherdruck/Throttling.
- **Qualität** — eine Use-Case-**Pack**-Eval (deterministisch erzeugt) + optionaler **LLM-as-judge**
  (gewichtete Master-Scorecard + Sicherheits-K.-o.).

Derselbe Code läuft auf M1 (LM Studio) und M5 (mlx_lm / mlx-openai-server) — **nur die Config wechselt**.
Output: `report.md` + `raw.csv` (Perf) bzw. ein Bundle aus `responses.jsonl`/`scores.csv`/`scorecard.md`
(Qualität).

## Wo finde ich was? (Diátaxis-Karte)

| Quadrant | Frage | Hier |
|---|---|---|
| **Reference** | „Welche Metrik/Spalte bedeutet was?" | [`reference/metrics-and-schema.md`](reference/metrics-and-schema.md) |
| **Explanation** | „Warum ist es so gebaut?" (Narrativ) | [`explanation/design-decisions.md`](explanation/design-decisions.md) |
| **Decisions** | „Welche Entscheidung, welche Alternativen, welche Folgen?" | [`decisions/README.md`](decisions/README.md) |
| **Historie (SDD)** | „Wie wurde Feature X entworfen/geplant?" | [`superpowers/specs/`](superpowers/specs) · [`superpowers/plans/`](superpowers/plans) |
| **Konventionen / Gotchas** | „Wie arbeite ich hier?" | `../AGENTS.md` |

> _Noch offen (Folge-Projekte): Tutorials, How-to-Guides, Hero-Bild (AGENTS.md „CORE-META-03/04")._

## Architektur in einem Blick

Ein geteilter Daten-Kontrakt (`models.py`); zwei entkoppelte Producer, per Zeitstempel gemergt.

```
config.py    YAML → validierte Config            prompts.py  Szenarien + deterministisches Token-Padding
runner.py    stream_once() Metrik-Ableitung · run_benchmark()   client.py  EINZIGE engine-bewusste Datei
sampler.py   entkoppelter Host-Sampler (psutil/powermetrics)    merge.py   Latenz × Ressourcen per Fenster
stats.py     P50/P95 · Median · CV%              report.py   raw.csv + report.md

# Qualitäts-Hälfte (Antwort-Qualität statt Tempo):
pack.py      Use-Case-Pack (YAML→validiert): Prompts + Flags + gewichtete Dimensionen + K.-o.  (Daten, kein Code)
qualrun.py   deterministischer Lauf: Matrix → Bundle      results.py  EvalResponse · Verdict · ModelReport
judge.py     pluggable LLM-as-judge (per-Antwort + holistische Master-Dimensionen + Safety-K.-o.)
scorecard.py Gewichtung/K.-o./Kategorie-Mathe → scorecard.md   aggregate.py  Cross-Run: Hardware×Qualität
runqueue.py  Nacht-Queue: viele Modelle sequenziell als isolierte Subprozesse

# Optional (Extra [gui]): out-of-process Control-Plane, misst nie im eigenen Prozess
gui/         FastAPI-Steuerzentrale; spawnt eval/judge als Subprozess, tailt events.jsonl; runs/ = SSOT
```

Die **tragenden Entscheidungen** hinter diesen Modulen (mit Alternativen + Auswirkungen) stehen in
[`decisions/`](decisions/README.md). Konventionen, Befehle und Gotchas: `../AGENTS.md`.
