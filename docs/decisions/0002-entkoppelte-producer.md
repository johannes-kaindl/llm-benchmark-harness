# ADR-0002: Zwei entkoppelte Producer + Merge per Zeitfenster

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Architektur

## Kontext

Auf Apple-Silicon ist der eigentliche Engpass der vereinheitlichte Speicher (Unified Memory), nicht die reine Rechenleistung: Solange Modell, KV-Cache und Kontext in den physischen RAM passen, ist die Latenz niedrig; jenseits der Schwelle beginnt der Kernel mit Kompression und Auslagerung, und die Antwortzeiten werden sprunghaft. Um einen langsamen Lauf eindeutig einer Ursache zuzuordnen — thermische Drosselung, Speicherdruck oder schlicht Modellgröße — muss der Harness Speicher und Throttling neben der Latenz messen.

Die treibende Kraft: Speicher und Throttling aus dem Request-Thread zu schätzen wäre verfälscht — derselbe Thread, der auf die Antwort wartet, kann den Host-Zustand nicht neutral messen. Gleichzeitig liefern die Host-Signale (`memory_pressure`, `pmset -g batt`, `powermetrics --samplers thermal`) ihre Daten asynchron und in eigenem Takt, nicht synchron pro Request.

## Entscheidung

Mess- und Ressourcen-Erfassung werden in **zwei entkoppelte Producer** getrennt, die später durch einen **Merge per Zeitfenster** zusammengeführt werden:

1. Der **Host-Sampler** (`HostSampler` in `touchstone/sampler.py`) läuft als eigener Prozess (`python -m touchstone.sampler`), tickt mit ~2 Hz (`interval` default `0.5` s) und schreibt zeitgestempeltes JSONL (`ResourceSample`). Vor der Schleife wird ein Baseline-Tick erfasst und als erste Zeile mit `baseline = True` geschrieben.
2. Der Latenz-Pfad erzeugt unabhängig davon `RunRecord`s mit `t_start`/`t_end`.
3. `touchstone/merge.py` verbindet beide Spuren erst nachträglich: `resources_for_window` / `merge_run` nehmen die Samples, deren `ts` in `[t_start, t_end]` fällt (beidseitig um `DEFAULT_TOLERANCE_S = 0.5` s erweitert), rollen sie auf (`peak_rss_mb`, `sys_used_mb`, `swap_delta_mb`, `mem_pressure_max`, `throttled`) und füllen die Ressourcen-Felder des Records.

## Erwogene Alternativen

- **Speicher/Throttling aus dem Request-Thread schätzen** — verworfen, weil der wartende Mess-Thread den Host-Zustand nicht neutral messen kann; das Ergebnis wäre verfälscht.
- **Auf einen engine-eigenen `/metrics`-Endpoint stützen** — verworfen, weil Host-Sampling engine-agnostisch ist und damit die Quelle der Wahrheit für Speicher bleibt; ein `/metrics`-Endpoint wäre nur Beiwerk.
- **Per-Prompt-KV-Isolation (jede Anfrage ihr eigenes Speicher-Fenster)** — bewusst aufgeschoben, weil sie ein deterministisches Entladen zwischen den Prompts erfordert, das ein fremder, dauerlaufender Endpoint nicht garantiert.

## Auswirkungen

- Positiv: Ein langsamer Lauf lässt sich eindeutig einer Ursache zuordnen (Drosselung, Speicherdruck oder Modellgröße), weil Latenz- und Ressourcen-Spur sauber per Zeitfenster pro Lauf korreliert werden.
- Positiv: Robust gegen Teil-Ausfälle — `load_samples_jsonl` toleriert eine halb geschriebene letzte Zeile (abgestürztes/wiederaufgenommenes Bundle); kurze Requests, die kein volles Sample-Tick überspannen, fallen auf das nächstgelegene Sample zurück, sodass eine Zeile nie leer bleibt.
- Trade-off / Restgrenze: Eine bewusste Unschärfe bleibt beim Modell-Delta (`Peak − Baseline`). Da der Harness einen bereits laufenden Endpoint treibt, misst das Delta bei vorab geladenem Modell die Inferenz-Last, bei faulem Nachladen aber auch das Gewicht; im Unified Memory lässt sich der KV-/Kontext-Anteil nicht sauber vom Gewicht trennen, daher wird er nicht dekomponiert.
- Trade-off / Restgrenze: Throttling per `powermetrics` erfordert `sudo`; ist es nicht verfügbar, bleibt der Throttle-Flag auf `False` statt fehlzuschlagen.

## Belege & Links
- Spec: `docs/superpowers/specs/2026-06-19-ndeval-harness-design.md` (§6: „Host sampling: the decoupled `HostSampler` … joined by time window (`merge`)") · Code: `touchstone/sampler.py`, `touchstone/merge.py` · Erklärung: `docs/explanation/design-decisions.md` („Warum zwei entkoppelte Prozesse")
- Verwandt: ADR-0001 (OpenAI-kompatibel als einzige Schnittstelle, sofern vorhanden)
