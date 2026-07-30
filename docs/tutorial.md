# Tutorial — vom frischen Klon zum ersten Modell-Vergleich

Dieses Tutorial führt einmal komplett durch: du installierst touchstone, misst ein lokales
Modell, lässt es qualitativ bewerten und liest am Ende zwei Artefakte, die du in eine
Vergleichsnotiz kopieren kannst. Es dauert je nach Maschine und Modell **30–60 Minuten**,
davon ist der größte Teil Wartezeit.

> Dies ist ein **Tutorial** (lernorientiert): es nimmt dir alle Entscheidungen ab und läuft
> geradeaus durch. Wenn du stattdessen eine konkrete Aufgabe lösen willst, geh in die
> [How-to-Guides](how-to/). Wenn du wissen willst, *warum* etwas so gebaut ist, siehe
> [Explanation](explanation/design-decisions.md) und [Decisions](decisions/README.md).

## Was du am Ende hast

- Ein `runs/<zeitstempel>/report.md` — die **Performance**-Tabelle: TTFT P50/P95,
  Decode-tok/s, Prefill-tok/s, Speicher-Peak und Throttle-Status deines Modells.
- Ein `runs/<zeitstempel>_eval_ndassist/scorecard.md` — die **Qualitäts**-Scorecard: gewichtete
  Bewertung über sieben Dimensionen, plus ein Gesamturteil mit Sicherheits-K.-o.

Beide sind so formatiert, dass ihre Spalten direkt in eine Vergleichstabelle passen.

## Voraussetzungen

- **macOS.** Der Host-Sampler liest `memory_pressure`, `pmset` und `powermetrics` — das sind
  macOS-Werkzeuge. Der Mess-Kern selbst ist plattformneutral, die Ressourcen-Hälfte nicht.
- **[uv](https://docs.astral.sh/uv/)** installiert (`brew install uv`).
- **Ein laufender lokaler LLM-Server** mit OpenAI-kompatibler API — LM Studio, `mlx_lm.server`,
  Ollama oder llama.cpp. Wenn du noch keinen hast: siehe
  [uplink.jkaindl.de/llm-setup](https://uplink.jkaindl.de/llm-setup).
- **Netzteil angeschlossen.** Auf Akku drosselt Apple Silicon die GPU um 30–50 %; touchstone
  flaggt solche Läufe und schließt sie aus den Aggregaten aus — du bekommst dann eine leere
  Tabelle. Das ist Absicht, aber als erster Lauf frustrierend.

## Schritt 1 — Installieren

```bash
git clone https://git.jkaindl.de/jkaindl/llm-benchmark-harness
cd llm-benchmark-harness
uv sync
uv run touchstone --help
```

Die letzte Zeile listet neun Kommandos (`run`, `embed`, `report`, `aggregate`, `eval`,
`judge`, `gui`, `queue`, `judge-meta`). Wenn sie erscheint, ist die Installation fertig.

## Schritt 2 — Endpoint starten und prüfen

Starte deinen Server und lade **ein** Modell. Für dieses Tutorial nehmen wir LM Studio auf
Port 1234; jeder andere OpenAI-kompatible Endpoint funktioniert genauso, nur die URL ändert
sich (`mlx_lm.server` → `:8080`, `mlx-openai-server` → `:8000`, Ollama → `:11434`).

Prüfe, dass der Endpoint antwortet und wie das Modell **exakt** heißt:

```bash
curl -s http://localhost:1234/v1/models | python3 -m json.tool
```

Notiere dir die `id` aus der Antwort — genau diese Zeichenkette kommt gleich in die Config.
Ein Tippfehler hier ist der häufigste Grund für einen Fehlschlag beim ersten Lauf.

## Schritt 3 — Eine eigene Config anlegen

touchstone hat **keinen** engine-spezifischen Code außer `client.py`. Ein Maschinen- oder
Server-Wechsel ist deshalb immer ein Config-Tausch, nie ein Code-Zweig. Kopiere die Vorlage:

```bash
cp config.example.yaml config.meine.yaml
```

Und ändere darin genau vier Dinge:

```yaml
endpoint:
  base_url: "http://localhost:1234/v1"   # dein Server
machine: "M1-16GB"                       # freier Bezeichner, landet im Report-Header
models:
  - { id: "qwen3-8b", quant: "Q5_K_M", max_tokens_default: 400 }   # id = exakt aus /v1/models
server_process_match: "LM Studio"        # Substring des Server-Prozesses, für Peak-RSS
```

Für den ersten Lauf lohnt es, die Matrix klein zu halten — die Voreinstellung fährt
4 Szenarien × 3 Kontext-Größen × 8 Wiederholungen und läuft entsprechend lange:

```yaml
runs_per_cell: 8
context_buckets: [4096]
scenarios: [bodydouble, compose]
```

> **Warum 8 und nicht 3?** Pro Zelle wird ein Warmup verworfen, throttled und Akku-Läufe
> fliegen zusätzlich raus. Unter 7 gewerteten Läufen markiert der Report die Zelle mit ⚠️,
> weil P95 dann kaum noch aussagt. touchstone warnt beim Start, wenn du unter 8 gehst.

## Schritt 4 — Der erste Performance-Lauf

```bash
uv run touchstone run --config config.meine.yaml
```

Was jetzt passiert — und warum es zwei Prozesse sind:

1. Ein **Host-Sampler** startet als **eigener Prozess** und schreibt fortlaufend
   Speicherdruck, Swap, Power-Source und Throttle-Status mit.
2. Der **Latenz-Runner** fährt die Matrix und misst pro Request TTFT, Prefill- und
   Decode-Durchsatz.
3. Danach werden beide **per Zeitstempel** zusammengeführt (`[t_start, t_end]`-Fenster pro
   Request).

Der Speicher wird bewusst **nie** aus dem Request-Thread geschätzt — das würde genau die
Zahl verfälschen, die du messen willst. Details dazu:
[ADR 0002](decisions/0002-entkoppelte-producer.md).

Willst du beim Laufen zusehen, hänge `--web` an — das öffnet einen Live-Monitor im Browser
(ein separater Beobachtungs-Prozess, er misst nichts).

## Schritt 5 — Den Report lesen

```bash
open runs/<zeitstempel>/report.md
```

Drei Dinge, die dich beim ersten Lesen überraschen könnten:

- **Es steht kein Mittelwert drin.** TTFT erscheint als **P50 / P95**, Decode und Prefill als
  Median, die Konsistenz als **CV %**. Grund: an der Speicher-Schwelle springt die Latenz auf
  Apple Silicon — ein Mittelwert verwischt genau den Effekt, wegen dem du misst
  ([ADR 0003](decisions/0003-verteilung-statt-mittel.md)).
- **Cold-Start steht separat.** Der allererste Request nach dem Laden beantwortet eine andere
  Frage („bleibt das Modell warm?") als der Dauerbetrieb und wird deshalb nicht eingemischt.
- **Peak-RAM ist System-Speicher, nicht RSS.** Auf Apple Silicon mappt mlx die Gewichte in den
  Unified Memory; die RSS des Server-Prozesses unterzählt das Modell dramatisch (~0,2 GB statt
  ~18 GB). Maßgeblich ist zusätzlich das **Modell-Delta** (Peak minus Baseline) — nur das ist
  über Maschinen hinweg vergleichbar.

Die Spalten `Qual.` und `Flow` bleiben absichtlich leer: sie sind für deine manuelle
Bewertung. Die automatische Qualitäts-Bewertung ist der nächste Schritt.

## Schritt 6 — Qualität messen: ein Pack laufen lassen

Performance sagt nicht, ob ein Modell *gut antwortet*. Dafür gibt es **Packs**: ein Pack ist
eine YAML-Datei mit Prompts, Green-/Red-Flags, gewichteten Bewertungs-Dimensionen und einer
Sicherheits-K.-o.-Regel. Zwei sind mitgeliefert — `ndassist` (Neurodivergenz-Assistent) und
`buero` (Büro- und Wissensarbeit).

```bash
uv run touchstone eval --pack packs/ndassist.yaml --config config.meine.yaml
```

Der Lauf ist **deterministisch** (Temperatur 0, fester Seed) und fährt jeden Prompt einmal pro
System-Prompt-Variante — bei `ndassist` sind das `baseline` (mit ND-System-Prompt) und `none`
(ohne). So misst derselbe Lauf gleich mit, **ob der System-Prompt überhaupt etwas bringt.**

Vorab prüft ein Pre-Flight, ob jedes Modell sichtbaren Content ausgibt. Reasoning-Modelle, die
alles ins `reasoning`-Feld schreiben und `content` leer lassen, würden sonst unbrauchbare
Zahlen liefern; du siehst dann eine Warnung ([ADR
0009](decisions/0009-reasoning-only-unscored-preflight.md)).

Wird der Lauf unterbrochen, setzt du ihn ohne Verlust fort:

```bash
uv run touchstone eval --pack packs/ndassist.yaml --config config.meine.yaml \
  --resume runs/<zeitstempel>_eval_ndassist
```

Am Ende liegt ein **Bundle** vor: `responses.jsonl` (die Antworten), `perf.csv` +
`resources.jsonl` (die Tech-Specs, automatisch mitgemessen), `bundle.json` (das Manifest) und
eine noch unbewertete `scorecard.md`.

## Schritt 7 — Bewerten lassen

Erzeugung und Bewertung sind **zwei getrennte Phasen**. Das Bundle aus Schritt 6 ist bereits
vollständig — du kannst es von Hand lesen, oder einen LLM-Judge darüberlaufen lassen:

```bash
cp judge.example.yaml judge.yaml     # Judge-Modell + Endpoint eintragen
uv run touchstone judge --bundle runs/<zeitstempel>_eval_ndassist --judge-config judge.yaml
```

Als Judge eignet sich ein **deutlich stärkeres** Modell als das getestete — lokal etwa ein
27B-Dense-Modell, oder ein Cloud-Endpoint für die beste Score-Qualität. Beides ist dieselbe
Konfigurationsdatei, der Judge ist austauschbar.

Zwei Dinge laufen dabei automatisch mit:

- **Thinking wird unterdrückt** (`suppress_thinking: true`). Ohne das produzieren hybride
  Reasoning-Modelle einen endlosen Gedankenstrom statt eines parsebaren Verdikts — und du
  müsstest in der LM-Studio-Oberfläche herumstellen ([ADR
  0011](decisions/0011-thinking-suppression.md)).
- **Ein Circuit-Breaker** bricht nach drei Fehlern in Folge ab, statt eine Nacht lang gegen
  einen kaputten Endpoint zu laufen.

Auch `judge` ist fortsetzbar: bereits bewertete Antworten werden übersprungen.

## Schritt 8 — Die Scorecard lesen

```bash
open runs/<zeitstempel>_eval_ndassist/scorecard.md
```

Die Scorecard hat vier Teile:

| Teil | Was er beantwortet |
|---|---|
| **Tech-Specs** | Wie schnell und wie speicherhungrig war das Modell *in genau diesem Lauf*? |
| **Master-Scorecard** | Gewichtete Bewertung pro Dimension, als Summe und in Prozent |
| **Per-Kategorie** | Wo ist das Modell stark, wo schwach (z. B. „Sensorik" vs. „Sicherheit")? |
| **Gesamturteil** | Empfehlung — inklusive **Sicherheits-K.-o.** |

Der K.-o. ist der eigentliche Punkt: fällt die Sicherheits-Dimension auf ≤ 2, gilt das Modell
als **nicht empfehlenswert**, egal wie gut die Gesamtprozente aussehen. Ein kleines Modell kann
flüssig, freundlich und trotzdem für diesen Einsatzzweck ungeeignet sein
([ADR 0008](decisions/0008-ko-safety-red-flag-scope.md)).

Vergleiche außerdem die beiden Prompt-Varianten miteinander: `baseline` gegen `none` zeigt dir
schwarz auf weiß, ob dein System-Prompt messbar hilft.

## Wie es weitergeht

Du hast jetzt einen vollständigen Durchlauf. Von hier aus:

- **Mehrere Bundles zusammenfassen** → `uv run touchstone aggregate --runs ./runs` erzeugt eine
  Hardware-×-Qualität-Tabelle über alle Läufe und Maschinen.
- **Dem Judge auf die Finger schauen** → `uv run touchstone judge-meta export --bundle <bundle>`
  schreibt eine Anfrage, die du durch eine stärkere (Cloud-)KI jagst; `judge-meta ingest` rechnet
  daraus Übereinstimmung und Begründungs-Qualität aus. Denn „wie gut bewertet mein lokaler Judge
  eigentlich?" ist eine eigene Messfrage
  ([ADR 0013](decisions/0013-judge-quality-meta-eval.md) ·
  [Auswertung](explanation/judge-quality-meta-eval-2026-06-28.md)).
- **Einen eigenen Einsatzzweck testen** → [Einen eigenen Pack bauen](how-to/eigenen-pack-bauen.md).
  Ein neuer Use-Case ist ein neues YAML, kein neuer Code.
- **Dasselbe auf einer zweiten Maschine messen** →
  [Eine neue Maschine einrichten](how-to/neue-maschine-einrichten.md).
- **Viele Modelle über Nacht** → [Die Nacht-Queue fahren](how-to/nacht-queue-fahren.md).
- **Lieber im Browser als im Terminal** → [Die Web-Steuerzentrale nutzen](how-to/gui-nutzen.md).
