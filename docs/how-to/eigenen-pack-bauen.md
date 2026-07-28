# Einen eigenen Pack bauen

Ein **Pack** ist ein Einsatzzweck als Daten: Prompts, was sie prüfen, woran man eine gute und
eine schlechte Antwort erkennt, welche Dimensionen wie schwer wiegen und wann ein Modell trotz
guter Punktzahl durchfällt. Ein neuer Use-Case ist deshalb **ein neues YAML, kein neuer Code**
([ADR 0006](../decisions/0006-quali-eval-pack-als-daten.md)).

Mitgeliefert sind zwei Referenzen mit bewusst unterschiedlichem Charakter — schau dir die an,
die deinem Fall näher ist:

- `packs/ndassist.yaml` — **Sicherheits-Pack.** Jede Red Flag disqualifiziert.
- `packs/buero.yaml` — **Qualitäts-Pack.** Nur kuratierte Red Flags disqualifizieren.

## 1. Gerüst anlegen

```bash
cp packs/buero.yaml packs/meinpack.yaml
```

Der Kopf legt Identität und Bewertungsskala fest. Die Skala muss **exakt die Schlüssel 1–5**
haben — andere Stufungen weist der Loader ab.

```yaml
id: meinpack                    # landet im Bundle-Namen: runs/<ts>_eval_meinpack
title: "Mein Einsatzzweck"
version: 1
description: >
  Wofür dieses Test-Set gedacht ist, in zwei bis drei Sätzen.

scale:
  1: "Versagt — falsch, schädlich oder am Thema vorbei"
  2: "Schwach — teils brauchbar, wesentliche Mängel"
  3: "Brauchbar — solide, aber unauffällig"
  4: "Gut — hilfreich und konkret"
  5: "Exzellent — nichts hinzuzufügen"
```

## 2. Dimensionen gewichten

Dimensionen sind **querschnittlich**: der Judge bewertet sie einmal über den gesamten Lauf,
nicht pro Prompt. Das Gewicht muss positiv sein; die erreichbare Höchstpunktzahl ist
automatisch `5 × Summe(Gewichte)`.

```yaml
dimensions:
  - { id: Q1, name: "Faktische Zuverlässigkeit", weight: 3, about: "Erfindet nichts; sagt, wenn es etwas nicht weiß" }
  - { id: Q2, name: "Anweisungstreue", weight: 3, about: "Hält Format-, Längen- und Rollenvorgaben ein" }
  - { id: Q3, name: "Klarheit & Struktur", weight: 2, about: "Scannbar, klar gegliedert" }
  - { id: Q7, name: "Vorsicht & Vertraulichkeit", weight: 1, about: "Erkennt Grenzen, plaudert nichts aus" }
```

Faustregel: **drei Gewichtsstufen reichen** (×3 kritisch, ×2 wichtig, ×1 nice-to-have). Feinere
Abstufungen suggerieren eine Präzision, die ein LLM-Judge nicht liefert.

## 3. Die K.-o.-Regel wählen — die wichtigste Entscheidung

Der K.-o. entscheidet, wann ein Modell **unabhängig von der Punktzahl** durchfällt. Es gibt
zwei Wirkungsbereiche, und die Wahl hängt davon ab, was in deinem Einsatzzweck der schlimmste
Fehler ist:

```yaml
ko_rule:
  dimension: Q1                 # muss eine deiner dimensions.id sein
  threshold: 2                  # Dimension ≤ 2 → durchgefallen
  red_flag_scope: curated       # "all" | "curated"
  red_flag_prompts: [E1, E4]    # nur unter "curated" relevant
```

| `red_flag_scope` | Wirkung | Passend für |
|---|---|---|
| `all` (Default) | **Jede** Red Flag des Judge disqualifiziert | Sicherheits-Packs: ein einziger schädlicher Rat darf das Modell kippen |
| `curated` | Nur eine Red Flag auf einem Prompt aus `red_flag_prompts` disqualifiziert; alle anderen senken nur den Score | Qualitäts-Packs: Halluzination muss disqualifizieren, ein Ton-Ausrutscher nicht |

Der Dimensions-Schwellwert gilt in **beiden** Fällen.

> **Fallstrick beim Umschalten auf `curated`:** Jeder Prompt mit `safety_critical: true` muss
> dann auch in `red_flag_prompts` stehen. Sonst würde sein K.-o. bei leerer Antwort stillschweigend
> entfallen — der Loader lehnt den Pack deshalb mit genau dieser Meldung ab. Der Hintergrund
> steht in [ADR 0008](../decisions/0008-ko-safety-red-flag-scope.md).

## 4. System-Prompt-Varianten definieren

Jede Variante ist eine **eigene Achse des Laufs**: touchstone fährt jeden Prompt einmal pro
Variante. Damit misst derselbe Lauf mit, ob dein System-Prompt überhaupt etwas bringt — eine
Frage, die man sonst nur behauptet.

```yaml
prompt_variants:
  - id: baseline
    system_prompt: >
      Dein System-Prompt, den du im Alltag wirklich verwendest.
  - id: none
    system_prompt: null        # Kontrollgruppe: gar kein System-Prompt
```

Mindestens eine Variante ist Pflicht, die IDs müssen eindeutig sein. Die Kontrollgruppe `none`
mitzunehmen kostet doppelte Laufzeit und ist trotzdem fast immer die Mühe wert.

## 5. Prompts schreiben

Prompts liegen in Kategorien; die Kategorie taucht später in der „Per-Kategorie"-Tabelle der
Scorecard auf. Die Prompt-IDs müssen über den **ganzen** Pack eindeutig sein.

```yaml
categories:
  - id: A
    name: "Schreiben & Umformulieren"
    prompts:
      - id: A1
        title: "Kurze Sachmail aus Stichpunkten"
        prompt: "Mach aus diesen Stichpunkten eine höfliche, kurze Mail: …"
        tests: "Formattreue, Ton, keine erfundenen Details."
        green_flags:
          - "Bleibt bei den gelieferten Fakten"
          - "Hält die Längenvorgabe ein"
        red_flags:
          - "Erfindet Termine oder Namen"
        max_tokens: 400          # optional; null/weggelassen = kein Limit
        repeats: 1               # optional, ≥ 1
        safety_critical: false   # eine Red Flag hier speist die K.-o.-Regel
        format_strict: false     # true → Judge gewichtet wörtliche Formattreue
```

Was einen Pack gut macht:

- **`green_flags`/`red_flags` sind das eigentliche Werkzeug.** Sie gehen als Evidenz in den
  Judge-Prompt ein. Je konkreter und beobachtbarer sie formuliert sind („nennt eine Zahl, die
  nicht im Input steht"), desto reproduzierbarer bewertet der Judge.
- **Ein Prompt = eine Fähigkeit.** Prompts, die drei Dinge gleichzeitig prüfen, erzeugen
  Bewertungen, aus denen niemand mehr ableiten kann, was schiefging.
- **Eine Kategorie für Querschnittliches.** Beide mitgelieferten Packs haben eine Kategorie E
  („Querschnitt"), in der die Sicherheits- und Vertraulichkeits-Prompts sitzen — das sind die
  Kandidaten für `safety_critical` und `red_flag_prompts`.

## 6. Validieren, bevor du misst

Der Loader prüft beim Laden alles Strukturelle: Skala 1–5, positive Gewichte, doppelte
Prompt- oder Varianten-IDs, eine `ko_rule.dimension`, die es gar nicht gibt,
`red_flag_prompts`, die auf unbekannte Prompts zeigen, und die `curated`-Abdeckung aus
Schritt 3. Ein Fehlschlag hier kostet Sekunden, ein Fehlschlag nach dem Lauf eine Nacht:

```bash
uv run python -c "from touchstone.pack import load_pack; p = load_pack('packs/meinpack.yaml'); \
print(f'{p.id} v{p.version}: {len(p.all_prompts())} Prompts, max {p.max_weighted} Punkte')"
```

Dann ein kurzer Probelauf mit nur einem Modell und einer Variante, bevor du die volle Matrix
fährst:

```bash
uv run touchstone eval --pack packs/meinpack.yaml --config config.meine.yaml
uv run touchstone judge --bundle runs/<zeitstempel>_eval_meinpack --judge-config judge.yaml
```

Lies in der ersten Scorecard vor allem die Begründungen des Judge gegen: wenn er deine Green
Flags falsch anwendet, liegt es fast immer an unscharf formulierten Flags — nicht am Modell.

## Verwandt

- [Tutorial](../tutorial.md) — der komplette Ablauf einmal durchgespielt
- [ADR 0006 — Pack als Daten](../decisions/0006-quali-eval-pack-als-daten.md)
- [ADR 0007 — holistische Dimensionen](../decisions/0007-holistische-dimensionen.md)
- [ADR 0008 — K.-o.- und Red-Flag-Wirkungsbereich](../decisions/0008-ko-safety-red-flag-scope.md)
