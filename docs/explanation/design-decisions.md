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

## Warum die GUI ein out-of-process Control-Plane ist

Eine persistente Steuerzentrale im **selben** Prozess laufen zu lassen wie die Messung würde
genau das Kern-Prinzip brechen, für das der Harness gebaut ist: Der Mess-Thread teilte sich
dann Event-Loop, Garbage-Collection und die schweren Web-Deps (FastAPI etc.) mit dem Server —
die Latenz wäre nicht mehr sauber messbar. Deshalb **spawnt** `ramcheck gui` die Messung als
eigenen Subprozess (`python -m ramcheck eval/judge`), genau wie die CLI heute den Host-Sampler
spawnt, und beobachtet sie nur über das Dateisystem (Tail von `events.jsonl`). Die GUI-Deps
liegen in einem optionalen `[gui]`-Extra und laden **nie** im Mess-Prozess; der Default-Pfad
ohne `--web`/`--emit-events` ist byte-identisch.

`runs/` bleibt die Single Source of Truth (MD/CSV/JSONL) — die GUI persistiert keine
Mess-Wahrheit, nur einen flüchtigen **Run-Sentinel** (`run.json`), der drei Rollen zugleich
trägt: run_dir-Handle (die GUI kennt das Verzeichnis beim Spawn), Cross-Process-Lock (nur ein
Mess-Lauf gleichzeitig, überlebt einen GUI-Neustart) und Discovery-Anker für laufende oder
abgestürzte Läufe. So bekommt man ein modernes, build-freies Frontend, ohne die Verfassung des
Messprozesses anzutasten — die Verfassung schützt die **Messung**, und die GUI ist keine
Messung, sondern nur Anzeige und Steuerung darum herum.

## Warum die Master-Dimensionen holistisch bewertet werden — und wie ein Urteil nachvollziehbar bleibt

Die Master-Dimensionen (Q1–Q7: Korrektheit, Ton, Sicherheit, …) sind **querschnittliche
Qualitäts-Achsen** über das gesamte Verhalten eines Modells, keine Eigenschaften einer
Einzelantwort. Der Judge bewertet sie deshalb **holistisch**: ein Urteil pro Dimension über
*alle* Antworten eines Modells, nicht eine Dimension-pro-Prompt-Matrix. Eine fest verdrahtete
„Dimension X wird von Prompt Y geprüft"-Zuordnung gibt es bewusst **nicht** — sie würde die
holistische Natur verfälschen (eine schwache Antwort drückt oft mehrere Dimensionen zugleich).

Damit „warum Q6 = 2?" trotzdem **lückenlos rückverfolgbar** ist, trägt die **Begründung** die
Nachvollziehbarkeit, nicht eine erfundene Struktur: der Judge **belegt** jede Dimensions-Bewertung
mit konkreten `prompt_id`s („Q6 = 2, weil bei E1 und C3 …"). Diese Zitate sind die Evidenz-Brücke —
im UI klickbar zur jeweiligen Antwort.

Die **K.-o.-Regel** (das Sicherheits-Gate) hat **zwei unabhängige Wurzeln**, die im UI je zu ihrer
echten Quelle verlinken: (a) die K.-o.-Dimension fällt unter die Schwelle (holistisch) → die belegte
Dimensions-Begründung; (b) ein als `red_flag` markierter Prompt wird red-geflaggt → die konkrete
per-Antwort-Begründung dieses Prompts. Ein „Nein" zeigt also, **welcher** Zweig feuerte.

So bleibt die Kette *Urteil → K.-o./gewichtete Master-Scorecard (Σ Score×Gewicht / Max) → belegte
Begründung → einzelne Antwort* durchgängig — obwohl die Bewertung holistisch ist. Diese Erklärung
ist auch **im Werkzeug selbst abrufbar** (Kriterien-/Ergebnis-Ansicht), damit die Zahlen nie bloße
Schlagwörter bleiben.

## Warum reasoning-only nicht als 1 zählt — und ein Pre-Flight davor warnt

Ein „Thinking"-Modell kann sein ganzes Token-Budget ins `reasoning`-Feld schreiben, bevor je
sichtbarer `content` kommt — die Antwort ist dann leer, obwohl das Modell „gearbeitet" hat. Das
früher verdrahtete „leerer content → 1/5" war hier **unfair**: die Ursache ist meist ein zu kleines
`max_tokens`, also *unser* Test-Setup, nicht die Qualität des Modells. Deshalb trennen wir zwei Fälle:
reasoning-only (leerer content, aber Reasoning vorhanden) wird **`unscored`** — aus dem Mittel genommen
und klar als Setup-Hinweis markiert, statt als stille 1 das Ergebnis zu verfälschen; eine wirklich leere
Ausgabe (auch kein Reasoning) bleibt 1 = unbrauchbar. Das Denken selbst wird bei leerem content
persistiert (`reasoning_text`), bleibt also einsehbar.

Die **eigentliche, elegante Lösung** ist aber, das Limit gar nicht erst zu setzen: Für die Eval lässt der
Harness Modelle **standardmäßig frei antworten** (`PackPrompt.max_tokens` default `None` → kein `max_tokens`
an die API → der Server entscheidet, kontextfenster-begrenzt). Das ist näher am echten Einsatz und erwürgt
ein Thinking-Modell gar nicht erst — der ursprüngliche Blocker entsteht so nie. Das feste Budget lebt nur
noch im **Latenz-Runner** (`run`), wo eine vergleichbare Generierungs-Last gewollt ist.

Wer für die Eval doch ein **kontrolliertes** sichtbares Budget will (kleine Maschine, Vergleich „bei gleichem
Budget"), setzt pro Prompt ein `max_tokens` — und gibt einem Thinking-Modell darüber hinaus mit
`reasoning_headroom_tokens` extra Denk-Raum *obendrauf* (das sichtbare Antwort-Budget bleibt der Cap, fairer
Vergleich), oder schaltet Thinking via `extra_body` ab. Diese Hebel sind **opt-in**; der Harness verändert die
Mess-Bedingung nie heimlich. Ein kleines Budget ist ein legitimes Test-Setup — der Pre-Flight macht den
Trade-off nur sichtbar.

Damit man das nicht erst nach einem 30-Minuten-Lauf merkt, prüft ein **Pre-Flight-Smoke** vor der Matrix
einmal pro Modell, ob das gewählte Setup sichtbaren content liefert. Er nutzt bewusst das **großzügigste**
Budget des Packs: liefert ein Modell selbst dann nur Reasoning, ist die Warnung sicher korrekt; enge
Einzel-Budgets, die nur manche Zellen leer lassen, fängt die per-Zelle-`unscored`-Logik. Der Smoke
**warnt** nur (er misst nichts und bricht nichts ab — ein falsch-negativer Smoke darf keinen validen Lauf
verhindern); allein `--strict-preflight` bricht hart ab.
