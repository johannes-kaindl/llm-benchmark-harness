# Explanation — warum es so gebaut ist

## Warum Verteilung statt Mittelwert

Auf Apple-Silicon ist der Engpass der **vereinheitlichte Speicher**, nicht die reine
Rechenleistung. Solange Modell + KV-Cache + Kontext in den physischen RAM passen, ist die
Latenz niedrig und vorhersehbar. Überschreitet man die Schwelle, beginnt der Kernel mit
Kompression und Auslagerung — die Antwortzeiten werden sprunghaft. Ein Mittelwert verwischt
genau das. Eine Maschine, die im Schnitt schnell ist, aber gelegentlich sekundenlang
einfriert, ist für interaktive Nutzung schlechter als eine etwas langsamere, die nie aus dem
Tritt kommt. Deshalb berichten wir **P50 + P95** für TTFT und **CV%** als Konsistenzmaß.

## Warum zwei entkoppelte Prozesse

Speicher und Throttling aus dem Request-Thread zu schätzen wäre verfälscht: derselbe Thread,
der auf die Antwort wartet, kann den Host-Zustand nicht neutral messen. Der **Host-Sampler**
läuft daher als eigener Prozess (`python -m ramcheck.sampler`), tickt mit ~2 Hz und schreibt
zeitgestempeltes JSONL. Erst **`merge.py`** verbindet Latenz- und Ressourcen-Spur per
Zeitfenster pro `run_id`. So lässt sich ein langsamer Lauf eindeutig einer Ursache zuordnen —
thermische Drosselung, Speicherdruck oder schlicht Modellgröße.

## Warum OpenAI-kompatibel als einzige Schnittstelle

LM Studio (M1) und mlx_lm/mlx-openai-server (M5) sprechen alle das OpenAI-Chat-Protokoll.
Indem der Hot-Path nur dieses Protokoll kennt (einzige engine-bewusste Datei: `client.py`),
läuft derselbe Code unverändert auf beiden Maschinen — der Maschinenwechsel ist ein
Config-Tausch, kein Code-Zweig. Host-Sampling ist die Quelle der Wahrheit für Speicher, weil
es engine-agnostisch ist; ein optionaler `/metrics`-Endpoint wäre nur Beiwerk.

## Warum Warmup verwerfen und Cold-Start separat

Der erste Request nach Server-/Modell-Start zahlt einmalige Kosten (Gewichte mappen, Caches
warm machen). Diese in die Verteilung zu mischen würde P95 und CV% verzerren. Also: **ein**
Warmup pro Zelle raus, und **eine** Cold-Start-TTFT einmalig und gesondert ausgewiesen — sie
beantwortet die eigene Frage „bleibt das Modell warm?".

## Warum gegen die echten `prompt_tokens` berichten

Tokenizer variieren pro Modell. Wir füllen Prompts deterministisch auf eine Ziel-Tokenzahl
auf, berichten aber gegen die tatsächlichen `prompt_tokens` aus `usage` — sonst würde der
Kontext-Bucket auf der x-Achse von der Tokenizer-Wahl abhängen statt von der Realität.
