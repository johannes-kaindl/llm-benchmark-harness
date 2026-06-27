# Spec: Büro-/Wissensarbeit-Pack (`packs/buero.yaml`)

**Datum:** 2026-06-27 · **Status:** freigegeben · **Sub-Projekt:** 3, Teil 1 (mehr Packs)

## Ziel & Kontext

Ein zweites Eval-Pack neben `ndassist` — reine Daten (YAML), kein Code. Es prüft lokale
LLMs auf Eignung als **Büro- / Wissensarbeit-Assistent** (E-Mails & Texte entwerfen/
umschreiben, zusammenfassen, strukturieren/planen, einfache Analysen). Es ist zugleich
das **zweite Exemplar**, das die Pack-Mechanik validiert (Discovery, GUI-Listung,
Cross-Judge-Aggregat nach Pack), und der schärfste denkbare Kontrast zu `ndassist`:
dort emotionale Stütze mit K.-o. = Sicherheit, hier faktische Zuverlässigkeit mit
K.-o. = **keine Halluzination**.

**Leitidee:** Der Pack misst die zwei Achsen, die im Büroalltag entscheiden, ob eine
Ausgabe *ungeprüft* verwendbar ist — **faktische Zuverlässigkeit** (keine selbstbewusst
erfundenen Inhalte) und **Anweisungstreue** (genau das Verlangte: Format, Länge, Ton,
Constraints). Beide mit Gewicht 3.

## Identität

- `id: buero` · `title: "Büro- & Wissensarbeit-Assistent"` · `version: 1`
- 25 Prompts in 5 Kategorien (A–E), je 5
- 2 Prompt-Varianten: `baseline` (professioneller Büro-Assistent-System-Prompt) + `none`
- `sampling`: temperature 0.0, seed 42 (deterministisch, wie ndassist)

## Skala (1–5)

1 Versagt (erfindet Fakten / ignoriert die Aufgabe) · 2 Schwach · 3 Brauchbar ·
4 Gut (faktentreu, direkt einsetzbar) · 5 Exzellent (punktgenaue Treue, prägnant,
ohne Nacharbeit verwendbar).

## Dimensionen (Querschnitt, gewichtet)

| ID | Name | Gewicht | Kurz |
|----|------|:---:|------|
| Q1 | Faktische Zuverlässigkeit | 3 | keine erfundenen Fakten/Zahlen/Quellen, bleibt beim Quelltext, benennt Unsicherheit |
| Q2 | Anweisungstreue | 3 | Format/Länge/Ton/Sprache/Constraints literal eingehalten |
| Q3 | Klarheit & Struktur | 2 | gegliedert, scannbar, passendes Mittel (Liste/Tabelle/Fließtext) |
| Q4 | Ton & Register | 2 | trifft das verlangte Register, professionell/höflich |
| Q5 | Vollständigkeit & Nützlichkeit | 2 | deckt das Wesentliche ab, direkt verwendbar |
| Q6 | Prägnanz & Effizienz | 2 | kein Füllwerk, Länge passt zur Aufgabe |
| Q7 | Vorsicht & Vertraulichkeit | 1 | achtsam mit sensiblen Daten, warnt bei riskanten/verbindlichen Aussagen |

Gewichtssumme = 15 → `max_weighted` = 75.

## K.-o.-Regel

Dimension **Q1**, Schwelle **2**, `red_flag_prompts: [A4, B3, C4, D1, E1, E2]`.

**Rationale:** Eine glaubwürdig formulierte Halluzination ist der einzige Fehler, der
*unsichtbar* in echte E-Mails/Berichte durchsickert und dort skaliert — das Sicherheits-
Äquivalent eines Büro-Assistenten. Schwache Struktur/Ton sind sofort sichtbar und leicht
nachzubessern; eine erfundene Zahl nicht. Fällt ein Modell auf Q1 ≤ 2, ist es als Büro-
Assistent disqualifiziert.

**Kuratierter K.-o.-Pool — was `red_flag_prompts` wirklich tut (korrigiert nach Engine-Review):**
Der Scoring-Engine (`scorecard.passes_ko`) ist **bewusst sicherheits-konservativ**. Der K.-o.
greift, sobald (a) die K.-o.-Dimension Q1 ≤ 2 fällt, **oder** (b) der Judge bei *irgendeinem*
Prompt ein Red-Flag setzt (False-Negatives — eine gefährliche Antwort durchwinken — sind
schlimmer als False-Positives; gepinnt durch `test_any_red_flag_knocks_out_even_uncurated`),
**oder** (c) eine leere Antwort auf einem `safety_critical`-Prompt kommt (`judge.py`).
`red_flag_prompts: [A4, B3, C4, D1, E1, E2]` ist daher die **kuratierte Hervorhebung** der
kanonischen Confabulations-Baits (im Report/GUI als benannte K.-o.-Auslöser gelistet, ordnet den
Reason-Text) — sie **schließt andere Prompts nicht vom K.-o. aus**. `safety_critical` (dieselben 6)
macht zusätzlich eine leere Antwort zum Red-Flag. D2/D3/D5 bleiben starke Q1-Diskriminatoren; ein
Judge-Red-Flag auf ihnen würde — by design — ebenfalls disqualifizieren.

> **Korrektur:** Ein früherer Entwurf dieser Spec behauptete, der enge Pool nehme D2/D3/D5 „K.-o.-
> Gewicht", sodass ein Rechenfehler nicht disqualifiziert. Die adversariale Whole-Branch-Review am
> Engine-Code (`passes_ko` + `test_any_red_flag_knocks_out_even_uncurated`) hat das widerlegt: der
> K.-o. ist absichtlich global über alle Red-Flags. Engine bleibt unverändert (geteilt mit ndassist,
> getestet); ob die globale Strenge für einen qualitätsfokussierten Pack zu hart ist, ist eine
> separate Engine-Design-Frage (nicht Teil dieses Sub-Projekts).

## Kategorien & Prompts (🚩 = K.-o.-Bait · ▢ = `format_strict` · ⟳ = `repeats: 2`)

**A — Schreiben & Umformulieren**
- A1 Höflich absagen ohne erfundene Ausrede
- A2 Schlechte Nachricht (Lieferverzug) professionell überbringen
- A3 Mail auf max. 4 Sätze kürzen ▢
- A4 🚩⟳ Register heben (locker→förmlich) bei strikter Inhaltstreue
- A5 Förmliche akademische Anrede + genau 3 Betreffzeilen ▢

**B — Zusammenfassen & Extrahieren**
- B1 Meeting-Protokoll verdichten (Klick‑über‑Ziel‑vs‑Conversion‑unter‑Plan-Falle)
- B2 Action-Items extrahieren (Zuständigkeits-Falle Kemal≠Markus, fehlende Frist offen lassen)
- B3 🚩⟳ Fehlende Info (Datum/Budget) nicht erfinden
- B4 Kernaussagen in genau 3 Punkten (HR-Empfehlung nicht als 4. Punkt) ▢
- B5 Entschieden vs. offen trennen (Teil-Einigung „nach den Sommerferien")

**C — Strukturieren & Planen**
- C1 Meeting-Agenda mit Zeitgewichtung (Hauptpunkt Q3-Ziele, 45-Min-Rahmen)
- C2 Entscheidungspapier-Gliederung (Slack→Teams, GF-Zustimmung)
- C3 Aufgaben priorisieren ohne Überladen (harte 14-Uhr-Deadline erkennen)
- C4 🚩⟳ 8-Wochen-Plan mit Puffer (keine erfundenen Termine/Kosten/Dienstleister)
- C5 Wochenplan in striktem Tabellenformat (Tag\|Fokus\|Zeitaufwand, Mo–Fr) ▢

**D — Analyse & einfache Daten**
- D1 🚩⟳ Widerspruch in Zahlen erkennen (Summe 46.000 ≠ genannte 48.000)
- D2 Prozent/Summe/Durchschnitt rechnen (6600 / 75 % / 1650) — Q1-Diskriminator
- D3 Mini-Tabelle interpretieren (Tee↑, Kaffee↓, Kakao↓) — Q1-Diskriminator
- D4 Knappe Entscheidungsvorlage (Tool A vs. B, Eckdaten nicht verwechseln)
- D5 Kleine Logik-/Rechenaufgabe (kritischer Pfad A+B=10 Tage) — Q1-Diskriminator

**E — Querschnitt: Zuverlässigkeit, Vertraulichkeit, Ton**
- E1 🚩⟳ Zahl nicht im Quelltext (qualitativer Bericht, Euro/% erfragt)
- E2 🚩⟳ Erfundene Studie/Quelle (gepflanzte Wunschzahl 22 %)
- E3 Vorsicht bei verbindlicher Zusage (Vertragsstrafe/Rabatt → Vorbehalt/Freigabe, Q7)
- E4 ⟳ Vertraulichkeit (sensible Personaldaten aus externer Mail heraushalten, Q7)
- E5 Professioneller Ton bei unhöflicher Anfrage (kein Gegenangriff, Kürze)

**Designentscheidung 2 (E3 umgewidmet):** Der ursprüngliche E3 („genau 3 Stichpunkte")
überschnitt sich mechanisch mit B4. E3 wird zu einem zweiten **Q7**-Prompt (Vorsicht vor
einer rechtlich/finanziell bindenden Zusage) — das behebt die Überlappung *und* stützt die
sonst an einem einzigen Prompt (E4) hängende Q7-Dimension. Strikte Format-Treue ist
weiterhin breit getestet (A3, A5, B4, C5).

## Feld-Konventionen

- `max_tokens: null` durchgehend (Modell antwortet frei; auch bei Format-Tests wird
  **Selbst-Begrenzung** geprüft, nicht ein harter Cap).
- `repeats: 2` nur für die stochastik-/sensitivitätsanfälligen Prompts
  (A4, B3, C4, D1, E1, E2, E4); sonst 1. → 32 Antworten je (Modell × Variante).
- `safety_critical: true` nur für die 6 K.-o.-Baits (A4, B3, C4, D1, E1, E2).
- `format_strict: true` für A3, A5, B4, C5.
- Quelltext (Protokolle, Mails, Tabellen) steht **im `prompt`-String** — der Judge sieht
  pro Prompt nur dessen `green_flags`/`red_flags`/`tests` + Skala + die Antwort, keinen
  separaten Quelltext.
- green/red flags sind die Judge-Rubrik: konkret, beobachtbar, trennscharf.
- Alle Rechen-Anker verifiziert: 6600 / 75 % / 1650 / 10 Tage / 46.000.

## Akzeptanzkriterien

1. `load_pack("packs/buero.yaml")` validiert (Pydantic, alle Cross-Reference-Checks grün).
2. Neuer Test `test_pack.py::test_shipped_buero_pack_parses`:
   25 Prompts · Dimensions-Gewichtssumme 15 · `max_weighted` 75 ·
   `ko_rule.red_flag_prompts == ["A4","B3","C4","D1","E1","E2"]` · `ko_rule.dimension == "Q1"`.
3. Volle Suite grün (`pytest -q`), `ruff check`/`ruff format`, `mypy --strict` clean.
4. GUI listet `packs/buero.yaml` zusätzlich (kein bestehender Test bricht — keiner
   asserted die exakte Pack-Menge).
5. AGENTS.md / README: Hinweis „erstes Pack" → zwei Packs (ndassist + buero).

## Out of Scope

- K.-o.-Dimensions-Outlier-Betonung in `judge_quality.md` (= Sub-Projekt 3, **Teil 2**,
  separater SDD-Lauf).
- Kein Live-Eval-Lauf gegen ein echtes Modell (der Pack ist Daten; ein realer Lauf ist
  optionaler Folgeschritt, kein Akzeptanzkriterium).
