# Eine neue Maschine einrichten

Ziel: auf einem zweiten Rechner Zahlen erheben, die mit denen vom ersten **wirklich
vergleichbar** sind. Der Code ist auf beiden identisch — was sich ändert, ist genau eine
Config-Datei. Das ist keine Bequemlichkeit, sondern die tragende Entscheidung des Projekts:
kein engine-spezifischer Code im Hot-Path ([ADR 0001](../decisions/0001-openai-only.md)).

## 1. Installieren und Endpoint feststellen

```bash
git clone https://codeberg.org/jkaindl/llm-benchmark-harness
cd llm-benchmark-harness
uv sync
curl -s http://localhost:1234/v1/models | python3 -m json.tool   # Port an deinen Server anpassen
```

Die `id` aus der Antwort ist die Zeichenkette, die gleich in `models` muss — nicht der Name,
den die Oberfläche anzeigt.

## 2. Config anlegen

Lege pro Maschine **eine eigene, eingecheckte Config** an (`config.<maschine>.yaml`).
`tests/test_config.py` hält eingecheckte Configs validierbar — allerdings über eine
**namentlich aufgezählte Liste**, nicht über einen Glob. Trägst du deine neue Datei dort nicht
ein, fällt eine kaputte Config erst beim Lauf auf.

```yaml
endpoint:
  base_url: "http://localhost:8080/v1"   # mlx_lm.server; LM Studio → :1234, mlx-openai-server → :8000
  api_key: "not-needed"

machine: "M5-64GB"                        # freier Bezeichner, erscheint im Report-Header
                                          # und ist die Vergleichsachse über Maschinen hinweg

models:
  - { id: "qwen3-8b", quant: "Q5_K_M", max_tokens_default: 400 }

server_process_match: "mlx_lm"            # Substring des Server-Prozesses (für Peak-RSS)
output_dir: "./runs"
power_check: true
```

### Diese Schlüssel bestimmen die Vergleichbarkeit

Sie **müssen** auf beiden Maschinen gleich sein, sonst vergleichst du Äpfel mit Birnen:

```yaml
runs_per_cell: 8
seed: 42
temperature: 0.0
context_buckets: [4096, 16384, 32768]
scenarios: [bodydouble, compose, rag_synth, longctx_stress]
```

Verfügbare Szenarien: `bodydouble` (kurzer Dialog), `compose` (längere Erzeugung),
`rag_synth` (Synthese über Kontext), `longctx_stress` (Kontext-Sättigung), `vlm` (Bild —
braucht ein vision-fähiges Modell, sonst weist der Endpoint den Request ab).

### Engine-Kennung explizit setzen

touchstone rät die Engine aus dem Port (`:1234` → `lm-studio`, `:8080`/`:8000` → `mlx`, sonst
`openai-compat`). Läuft dein Server auf einem anderen Port, oder willst du die Version im
Report-Header stehen haben, setze beides von Hand — die OpenAI-kompatible API gibt Build-
Versionen nicht her, `engine_version` bleibt sonst dauerhaft `unknown`:

```yaml
engine: "mlx"
engine_version: "mlx-lm 0.28.3"
```

## 3. Throttle-Erkennung scharf schalten

Ohne diesen Schritt bleibt der Throttle-Flag **stumm auf `False`** — throttled Läufe werden
dann nicht ausgeschlossen, sondern still mitgemittelt. Das ist der Unterschied zwischen
„keine Drosselung gemessen" und „Drosselung nicht messbar", und er ist von außen nicht
sichtbar.

`sampler.py` ruft `sudo -n powermetrics` auf. Erlaube das passwortlos:

```bash
sudo visudo -f /etc/sudoers.d/powermetrics
```

```
<dein-benutzername> ALL=(root) NOPASSWD: /usr/bin/powermetrics
```

Gegenprobe:

```bash
sudo -n powermetrics --samplers smc -n 1 -i 200 >/dev/null && echo "ok: passwortlos"
```

Willst du das nicht einrichten, setze `power_check: false` und **vermerke im Report-Kontext,
dass Throttling auf dieser Maschine nicht erkannt wird.** Eine unbemerkt gedrosselte Maschine
gegen eine ungedrosselte zu vergleichen ist der teuerste Fehler in dieser Messreihe.

## 4. Ans Netzteil

Auf Akku drosselt Apple Silicon die GPU um 30–50 %. touchstone erkennt das über `pmset`,
flaggt die Läufe und **schließt sie aus den Aggregaten aus** — die Tabelle bleibt dann leer
(`n=0`), die Rohdaten stehen weiter in `raw.csv`. Das ist gewollt, aber es sieht wie ein Bug
aus, wenn man es nicht weiß.

## 5. Probelauf, dann echter Lauf

Erst klein, um Config-Fehler in zwei Minuten statt in einer Stunde zu finden:

```bash
uv run touchstone run --config config.<maschine>.yaml --out ./runs/_probe
```

Prüfe im entstandenen `report.md` den Kopf: stimmen Maschine, Chip, RAM, Engine und
Power-Source? Der Chip- und RAM-Wert kommt automatisch aus der Host-Erkennung — wenn er nicht
zu deiner Erwartung passt, ist meist das `machine:`-Label falsch, nicht die Erkennung.

Dann der volle Lauf:

```bash
uv run touchstone run --config config.<maschine>.yaml
```

## 6. Über Maschinen hinweg vergleichen

Kopiere die `runs/`-Verzeichnisse beider Maschinen an einen Ort und lass die Tabellen neu
bauen:

```bash
uv run touchstone report    --runs ./runs      # Performance über alle raw.csv
uv run touchstone aggregate --runs ./runs      # Hardware × Qualität über alle scores.csv
```

Beim Lesen: **vergleiche das Modell-Delta, nicht den System-Peak.** Der rohe Peak enthält das
gesamte Betriebssystem und jeden anderen Prozess und ist zwischen zwei Rechnern bedeutungslos.
`sys_used_delta_mb` (Peak minus Baseline) ist die Zahl, die über Maschinen hinweg trägt.

## Verwandt

- [Reference — Metriken, CSV-Schema, Config-Schlüssel](../reference/metrics-and-schema.md)
- [ADR 0001 — nur OpenAI-kompatibel](../decisions/0001-openai-only.md)
- [ADR 0004 — Modell-Delta statt System-Peak](../decisions/0004-modell-delta.md)
- [Die Nacht-Queue fahren](nacht-queue-fahren.md) — mehrere Modelle unbeaufsichtigt
