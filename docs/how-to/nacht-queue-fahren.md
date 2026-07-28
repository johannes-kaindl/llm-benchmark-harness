# Die Nacht-Queue fahren

Ein einzelner Pack-Lauf plus Bewertung dauert je nach Modell Stunden. Willst du fünf Modelle
vergleichen, willst du das nicht von Hand nacheinander anstoßen. Die **Queue** fährt mehrere
Einträge sequenziell durch, jeder als `eval` → optional `judge`, unbeaufsichtigt und nach
Abbruch fortsetzbar ([ADR 0015](../decisions/0015-nacht-queue.md)).

Der Kern ist nicht die Automatisierung, sondern die **Isolation**: ein Modell pro Eintrag,
zwischen den Einträgen wird das vorige entladen und gewartet, bis der System-RAM wieder
abgesunken ist. Ohne das misst du beim zweiten Modell die Reste des ersten mit, und das
Modell-Delta wird wertlos.

## 1. Queue-Datei schreiben

```bash
cp queue.example.yaml queue.yaml
```

```yaml
defaults:
  reset_command: "lms unload --all"   # zwischen Einträgen; "" schaltet es ab
  settle: { timeout_s: 120, plateau_polls: 3, poll_interval_s: 2, epsilon_mb: 200 }
  step_timeout_s: { eval: 14400, judge: 21600 }   # Watchdog in Sekunden
  cooldown_s: 0                                    # optionale Extra-Pause nach settle

entries:
  - config: config.m5-lmstudio.yaml
    pack: packs/buero.yaml
    model: { id: "google/gemma-4-12b" }
    judge_config: judge.yaml

  - config: config.m5-lmstudio.yaml
    pack: packs/buero.yaml
    model: { id: "qwen/qwen3.6-27b" }
    judge_config: judge.yaml
```

Jeder Eintrag darf `reset_command`, `settle`, `step_timeout_s` und `cooldown_s` einzeln
überschreiben; ohne Angabe gilt der Wert aus `defaults`. Lässt du `judge_config` weg, wird der
Eintrag nur erzeugt und bleibt unbewertet — du kannst ihn später einzeln bewerten lassen.

### Was `settle` tatsächlich tut

Nach dem `reset_command` pollt touchstone den System-RAM alle `poll_interval_s` Sekunden und
wartet, bis sich der Wert über `plateau_polls` Messungen hinweg um weniger als `epsilon_mb`
bewegt — oder bis `timeout_s` erreicht ist. Erst dann startet der nächste Eintrag. Ein zu
knappes `epsilon_mb` lässt die Queue unnötig lange warten, ein zu großes startet das nächste
Modell, während das vorige noch Speicher hält.

## 2. Erst die Wechsel-Kette prüfen — nie ohne

```bash
uv run touchstone queue --queue queue.yaml --check
```

Das ist der wichtigste Schritt und dauert Minuten statt Stunden. `--check` fährt **keine
Matrix**: es geht pro distinktem Modell einmal durch reset → settle → **ein** winziger Request
und protokolliert, ob das Modell geladen und wieder entladen wurde, samt RAM davor und danach.

Ergebnis ist `runs/<zeitstempel>_check/check.md`. Lies es gegen, bevor du eine Nacht
investierst — typische Funde:

- Ein Modell-Bezeichner stimmt nicht exakt mit dem im Server überein (der häufigste Fall).
- `lms` ist nicht im `PATH` des nicht-interaktiven Prozesses, das Entladen passiert also nie.
- Der RAM sinkt zwischen den Einträgen nicht ab — dann greift `reset_command` nicht, und alle
  Modell-Deltas nach dem ersten Eintrag wären verfälscht.

## 3. Scharf schalten

```bash
uv run touchstone queue --queue queue.yaml
```

Angelegt wird `runs/<zeitstempel>_queue/`; die Bundles der Einträge landen wie gewohnt daneben
in `runs/`. Die `step_timeout_s`-Watchdogs beenden einen hängenden Schritt, statt die Queue bis
zum Morgen blockieren zu lassen — `judge` ist deutlich langsamer als `eval`, deshalb der höhere
Default.

## 4. Nach einem Abbruch fortsetzen

```bash
uv run touchstone queue --queue queue.yaml --resume runs/<zeitstempel>_queue
```

Fertige Einträge werden übersprungen. Weil auch `eval` und `judge` selbst inkrementell sind,
verlierst du selbst mitten in einem Eintrag höchstens die gerade laufende Antwort.

## 5. Am Morgen auswerten

```bash
uv run touchstone aggregate --runs ./runs
```

Das rollt alle `scores.csv` zu **einer** Hardware-×-Qualität-Tabelle zusammen — die Sicht, für
die die Queue eigentlich existiert: mehrere Modelle, gleiche Maschine, gleicher Pack,
nebeneinander.

## Vor der ersten echten Nacht

- **Netzteil.** Akku-Läufe werden geflaggt und aus den Aggregaten ausgeschlossen; eine ganze
  Nacht auf Akku produziert eine leere Tabelle.
- **Ruhezustand aus.** `caffeinate -i uv run touchstone queue --queue queue.yaml` verhindert,
  dass die Maschine mitten in der Queue einschläft.
- **Judge-Endpoint erreichbar.** Läuft der Judge auf derselben Maschine, konkurriert er mit
  dem getesteten Modell um Speicher — plane ihn entweder in die `settle`-Rechnung ein oder
  bewerte im Anschluss in einem zweiten Durchgang.
- **Platz.** Jedes Bundle enthält alle Rohantworten; ein Dutzend Einträge summiert sich.

## Verwandt

- [ADR 0015 — Nacht-Queue](../decisions/0015-nacht-queue.md)
- [ADR 0004 — Modell-Delta statt System-Peak](../decisions/0004-modell-delta.md) — warum die
  Isolation zwischen den Einträgen der Punkt ist
- [ADR 0010 — Judge-Runaway-Guard](../decisions/0010-judge-runaway-guard.md) — warum ein
  unbeaufsichtigter Judge einen Circuit-Breaker braucht
