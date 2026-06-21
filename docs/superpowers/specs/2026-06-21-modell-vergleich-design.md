# Modell-Vergleich (Effizienz-Relation) — Design (Ink. 8, Phase 2 · Schnitt 1)

**Datum:** 2026-06-21
**Status:** ratifiziert (Brainstorming abgeschlossen) + nach adversarialer Code-Review geschärft — vor Implementierungsplan
**Scope:** Erster Schnitt von Phase 2 („das Labor bedienen"): eine **kontrollierte Vergleichs-Ansicht entlang einer Achse innerhalb eines Bundles** (`model` oder `variant`), die **Leistung = Qualität in Relation zu Kosten** (Geschwindigkeit + RAM + CPU) zeigt.
**Baut auf:** [`2026-06-21-gui-nachvollziehbarkeit-design.md`](2026-06-21-gui-nachvollziehbarkeit-design.md) (Ink. 7) — nutzt `bundle_detail`, `master_rows`+`reports`, die Drill-down-Nachvollziehbarkeit, das `resources.jsonl`-CPU-Sampling, den line-toleranten `load_samples_jsonl`.
**Phase 2 später (eigene Specs):** Cross-Bundle-/Hardware-Vergleich (M1 ↔ M5) · Variablen-Entkopplung · im Tool editieren · Steuerung brauchbar.

## 1 · Ziel & Motivation

Phase 1 macht *ein* Ergebnis nachvollziehbar; das Labor lebt vom **Vergleich**: „Modell X taugt für Aufgabe B *besser als* Y". Johannes' erstes Experiment: **dasselbe Pack mit zwei Modellen.**

Schlüssel-Einsicht (verifiziert): das braucht **keine** Variablen-Entkopplung. Die Modell-Achse ist schon entkoppelt — `Config.models` ist eine Liste, `iter_eval_cells` fährt `for model in config.models`; ein eval mit zwei Modellen erzeugt Antworten/Verdicts für beide (gleicher Seed/HW/Pack — maximal kontrolliert). Dasselbe gilt für `variant`: das vorhandene `ndassist`-Bundle hat schon `baseline` vs `none`. Es fehlt nur die **Vergleichs-Ansicht**.

Kriterium: **Leistung = Arbeit pro Kosten** — Qualität in **Relation** zu Kosten, nicht isoliert. „Kosten" ist mehrdimensional: Geschwindigkeit **und** Ressourcen-Last (RAM-Peak/Druck + CPU — der Apple-Silicon-Engpass).

## 2 · Ratifizierte Entscheidungen (mit Begründung)

| # | Entscheidung | Begründung |
|---|---|---|
| **V1** | **Vergleich entlang `model` \| `variant` *innerhalb eines Bundles*** — keine Entkopplung. | Achsen schon entkoppelt; Daten entstehen aus einem Multi-Modell-/Multi-Varianten-eval. YAGNI. |
| **V2** | **Leistung = Qualität in Relation zu Kosten (Geschwindigkeit + RAM + CPU). Keine** verdichtete Effizienz-Kennzahl. | Eine einzelne Zahl müsste Zeit gegen RAM gewichten (anwendungsabhängig). Die Relation wird *gezeigt* (Scatter + explizite Kosten-Zeilen + Fazit in Worten). |
| **V3** | **Drei-Schichten-Ansicht** (Mockup ratifiziert): ① Effizienz-Relation (Scatter) · ② Kopf-an-Kopf · ③ Drill-down. **N-fähig** (Scatter N; Kopf-an-Kopf 2–3). | Überblick → Detail → Wurzel, gespiegelt an Phase 1. |
| **V4** | **Drill-down nutzt die Phase-1-Nachvollziehbarkeit** — zu beiden Antworten + **per-Prompt-`Verdict.rationale`** (immer vorhanden). | Erbt die Rückverfolgbarkeit. (Dim-level `dim_rationales` sind nur vorhanden, wenn das Bundle ein `reports.jsonl` hat — sonst „Begründung nicht erfasst", Phase-1-Konvention.) |
| **V5** | **Hardware-/Cross-Bundle-Vergleich NICHT in diesem Schnitt.** | Andere Datenquelle (`aggregate`) + Mechanik. Nächster Schnitt. |
| **V6** | **CPU-Last pro Achsenwert GUI-seitig aus `resources.jsonl`** (Ticks im `[t_start,t_end]`-Fenster der Antworten → Max/Ø), ohne `EvalResponse`/`merge` zu ändern. **Realität: kein Bestands-Bundle hat `cpu_pct`** → CPU ist heute überall leer; erst ein frischer eval füllt es. **„n. v." ist ein first-class, getesteter Zustand.** | Mechanismus verifiziert (Fenster zeitlich disjunkt → Tick eindeutig zuordenbar). Aber `cpu_pct` kam mit Ink.7; kein persistiertes Bundle trägt es. Vermeidet eine Datenmodell-Änderung im ersten Schnitt. |
| **V7** | **③-Divergenz ist eine *per-Aufgabe*-Sicht** (per-Prompt `Verdict.score`-Δ), **orthogonal** zur holistischen Qualitäts-% — *nicht* deren Herleitung. UI rahmt es als „wo gehen die Modelle bei *einzelnen Aufgaben* auseinander". | Die % kommt aus den **holistischen** `dim_scores` (separater Judge-Call), nicht aus per-Prompt-Verdicts — die beiden sind mathematisch entkoppelt. Sie als „die Prompts, die die %-Lücke treiben" zu verkaufen wäre eine Lüge. Per-Prompt-Δ ist trotzdem die nützlichste konkrete „wo unterscheiden sie sich"-Sicht. |

## 3 · Die Vergleichs-Ansicht — `/compare/{bundle}?axis=model\|variant`

Neue Route je Bundle. Die **Achsenwerte** kommen aus `scorecard.model_variant_groups(responses)` (funktioniert auch un-judged). **Default-Achse:** `model` wenn >1 Modell, sonst `variant`. `axis=model` ist nur sinnvoll bei `models>1` (sonst der ein-Achsenwert-Zustand). Drei Schichten:

1. **① Effizienz-Relation.** Inline-SVG-**Scatter**: x = Decode tok/s (Median), y = Qualität-%, **Punktgröße = Peak-RAM (`sys_used_mb`)**; ein Punkt je Achsenwert. Daneben ein **Relations-Fazit in Worten**, aus den Zahlen generiert. Bei einem Achsenwert: Hinweis „nur ein {Achse} im Bundle — nichts zu vergleichen".
2. **② Kopf-an-Kopf.** Spalte je Achsenwert: Urteil-Badge + Qualität-% (aus `master_rows`) · die gewichteten **Dimensionen** (aus `reports`/`ModelReport.dim_scores`, inkl. K.-o.-Markierung) · Decode · TTFT P50 · `e2e_s` Median · Peak-RAM (`sys_used_mb`) + Druck · CPU Ø/Max (oder „n. v."). **Gewinner je Zeile** markiert. Bei `axis=model`: die **projizierte Variante** ist sichtbar angeschrieben (siehe §4).
3. **③ Drill-down — „wo gehen sie bei einzelnen Aufgaben auseinander" (per-Prompt, V7).** Prompts nach |Δ `Verdict.score`| sortiert; Klick öffnet die Antworten **beider** Achsenwerte nebeneinander + ihre per-Prompt-`Verdict.rationale` (zweispaltige Phase-1-Antwort-Darstellung). Klar als *per-Aufgabe*-Sicht beschriftet, nicht als Herleitung der %.

## 4 · Datenquelle & Aggregation

Alles aus dem **vorhandenen** Bundle (Phase-1-Loader), **ohne** `EvalResponse`/`merge` zu ändern. `compare_detail(run_dir, axis)` ermittelt die Achsenwerte (`model_variant_groups`), gruppiert nach Achsenwert und berechnet je Wert:

- **Qualität:** **Join** von `master_rows` (`pct`, `recommendation`, `safety_passed`, `safety_reason` pro (model,variant)) **mit der parallelen `reports`-Liste** (`ModelReport.dim_scores` + `dim_rationales`) auf den (model,variant)-Key — wie `scorecard.render_scorecard_md` joint. **`master_rows` trägt kein `dim_scores`** (nur das %). **Auf reports.jsonl-losen Bundles** (alle heutigen): `dim_scores` aus `scores.csv` rekonstruiert vorhanden, `dim_rationales` **leer** → „Begründung nicht erfasst".
- **Geschwindigkeit + RAM:** **direkt** über die OK/non-cold-Antworten des Achsenwerts berechnet (nicht `_perf_summary`, das nur `ttft_p50/p95`, `decode_med`, `peak_ram_gb` liefert): `decode_tps` Median, `ttft_s` P50, `e2e_s` Median; **Peak-RAM = `max(sys_used_mb)`** (System-Speicher — Mess-Verfassung „Peak-RAM = system, not RSS"; `peak_rss_mb` undercountet mmap'd weights), `mem_pressure_max` via `models.pressure_max`.
- **CPU (V6):** `merge.load_samples_jsonl` → Ticks, deren `ts` in *irgendein* `[t_start,t_end]`-Fenster der Antworten des Achsenwerts fällt → `cpu_pct` Max/Ø (None-Ticks gefiltert). **Heute überall leer** (kein Bundle hat `cpu_pct`) → „n. v.".
- **`axis=model`-Varianten-Projektion (entschieden, nicht offen):** zeigt eine **einzelne** Variante je Modell, **default `baseline` wenn vorhanden, sonst die erst-gesehene**; die gewählte Variante ist in der Ansicht **angeschrieben** („Modelle bei Variante: baseline"). Umschaltbar, wenn mehrere Varianten existieren. (Auf realen Daten heute nicht auslösbar — kein Multi-Modell-Bundle —, daher per **synthetischer 2-Modell×2-Varianten-Fixture** getestet, §8.)
- **Divergenz (③, per-Prompt, V7):** je Prompt die per-Antwort `Verdict.score` der Achsenwerte, nach |Δ| sortiert; bei `repeats>1` Mittel je (model,variant,prompt_id). Orthogonal zur holistischen %.

## 5 · Read-/Render-Schicht

- `ramcheck/gui/compare.py` *(neu)* — `compare_detail(run_dir, axis)` (§4) + `_cpu_for_window(samples, responses)` (V6) + `_relations_summary(values)` (Text) + Divergenz-Sortierung. Pure, unit-testbar.
- `ramcheck/gui/app.py` — Route `/compare/{bundle}` (confined wie `/result`); `axis`-Query gegen `{model, variant}` validiert; `variant`-Projektion als Query bei `axis=model`.
- `ramcheck/gui/templates/compare_axis.html` *(neu)* — die 3 Schichten.
- `ramcheck/gui/static/scatter.js` *(neu)* — Inline-SVG-Scatter (x=Speed, y=Qualität, r=RAM), build-frei.
- `result.html` / `overview.html` — Link „↔ Vergleichen ({Achse})", nur wenn >1 Achsenwert (aus `model_variant_groups`, auch eval-only).
- **Abgrenzung:** die bestehende `/compare` (Cross-Run-`aggregate`-Tabelle) bleibt unverändert; der neue Innerhalb-Bundle-Vergleich ist `/compare/{bundle}`. Menü-/Namensführung im Plan.

## 6 · Navigation / Kohärenz

`Übersicht/Ergebnis → „↔ Vergleichen" (nur >1 Achsenwert) → /compare/{bundle} → ② Zeile → ③ per-Prompt-Divergenz → beide Antworten + Verdict-Begründungen → zurück`. Achse umschaltbar (`model`↔`variant`) wenn beide >1 Wert. Keine Sackgassen.

## 7 · Error-Handling

- **Nur ein Achsenwert** (z. B. `axis=model` bei 1 Modell): klare „nichts zu vergleichen"-Meldung; der „↔ Vergleichen"-Link erscheint dann gar nicht.
- **Un-judged Bundle:** keine `master_rows`/`reports` → Relation zeigt nur Perf/Kosten + „noch nicht bewertet"; ③ entfällt.
- **CPU leer (alle heutigen Bundles):** „n. v."-Spalte, Scatter/Rest unverändert — **first-class getestet**, kein Edge-Case.
- **Keine `reports.jsonl`:** `dim_scores` aus `scores.csv`, `dim_rationales` „Begründung nicht erfasst"; ② Dimensionen + ③ per-Prompt-Verdicts funktionieren trotzdem.
- **Korrupte `resources.jsonl`:** `load_samples_jsonl` ist line-tolerant (Phase 1); CPU degradiert, Seite bleibt. Defensiv um die Gruppen-Schleife (eine korrupte Gruppe 500t nie die Seite).

## 8 · Teststrategie (TDD)

**Unit (pure):**
- `compare_detail`: gruppiert nach `model`/`variant`; Qualität via `master_rows`⨝`reports`-Join (nicht `dim_scores` aus `master_rows`); Speed/RAM direkt berechnet (Peak-RAM = `sys_used_mb`); ein-Achsenwert-Fall markiert „nichts zu vergleichen". Hermetisches Bundle (echtes `packs/ndassist.yaml` + synthetische responses/judgements/`reports.jsonl`/`resources.jsonl`).
- **Synthetische 2-Modell×2-Varianten-Fixture** für `axis=model`-Projektion (kein reales Bundle deckt das ab): default-Variante `baseline`, sichtbar angeschrieben, umschaltbar.
- `_cpu_for_window` (V6): Tick-Zuordnung korrekt; **None-Ticks → CPU „n. v."** (der heutige Real-Zustand) als expliziter Test; ein synthetischer Tick mit `cpu_pct` → Max/Ø stimmen.
- `_relations_summary`: erzeugt die Vergleichs-Worte aus 2–3 Wert-Sätzen.
- Divergenz: Prompts nach |Δ `Verdict.score`|, `repeats>1` gemittelt.

**Integration / Verhalten:**
- `TestClient`: `/compare/{bundle}?axis=variant` (hermetisches 2-Varianten-Bundle) rendert beide Spalten + Scatter + Fazit + per-Prompt-Drill-down-Link; `axis=model` (2×2-Fixture) zeigt die projizierte Variante angeschrieben; `axis=bad` → 404/422; ein-Achsenwert → Hinweis; un-judged → Perf-only; CPU-leer → „n. v.". `result.html` zeigt „↔ Vergleichen" nur bei >1 Achsenwert.
- **Rückwärtskompat:** `/compare`-Aggregat unverändert; Kern ohne `[gui]` funktionsfähig.

**Manuell (am Ende):** gegen das **echte** `ndassist`-Bundle `/compare/<bundle>?axis=variant` (baseline vs none) — Scatter trennt beide, Kopf-an-Kopf zeigt Qualität + Speed + RAM (**CPU = „n. v."**, da Bestands-Bundle), Drill-down öffnet divergierende Antworten + Verdict-Begründungen. Für `axis=model` + nicht-leere CPU: ein **frischer** 2-Modell-eval (schreibt `cpu_pct`).

## 9 · Scope-Grenze (YAGNI)

**In diesem Schnitt:** `/compare/{bundle}` (3 Schichten) entlang `model`/`variant`; GUI-seitige CPU-Zuordnung (V6, heute „n. v."); Relations-Fazit; Verlinkung + per-Prompt-Drill-down; Empty-/Alt-Bundle-States; 2×2-Fixture.

**Bewusst NICHT:** Cross-Bundle-/Hardware-Vergleich; verdichtete Effizienz-Kennzahl; Variablen-Entkopplung; im Tool editieren; Multi-Modell-Lauf *starten* aus dem UI (die Ansicht braucht nur vorhandene Daten); `cpu_pct` in die gemergten `EvalResponse`-Felder/`RAW_CSV_COLUMNS` heben; ③ als Herleitung der holistischen % (V7 — bewusst getrennt).

## 10 · Berührte / neue Dateien

| Datei | Änderung |
|---|---|
| `ramcheck/gui/compare.py` | **neu** — `compare_detail` + `_cpu_for_window` + `_relations_summary` + Divergenz (pure); Qualität via `master_rows`⨝`reports` |
| `ramcheck/gui/app.py` | **neu** Route `/compare/{bundle}` (confined, `axis`+`variant` validiert); „↔ Vergleichen"-Link-Daten |
| `ramcheck/gui/templates/compare_axis.html` | **neu** — 3-Schichten-Ansicht |
| `ramcheck/gui/templates/{result,overview}.html` | „↔ Vergleichen ({Achse})"-Link, nur bei >1 Achsenwert |
| `ramcheck/gui/static/scatter.js` | **neu** — Inline-SVG-Scatter (x=Speed, y=Qualität, r=RAM) |
| `tests/` | neue Tests je §8 (inkl. 2×2-Fixture, CPU-„n. v."-State) |
| `AGENTS.md` | `/compare/{bundle}` (Innerhalb-Bundle-Achsen-Vergleich) vs. `/compare` (Cross-Run-Aggregat) abgrenzen |

## 11 · Offene Detailpunkte (für den Implementierungsplan)

- **Scatter vs. erweitertes `sparkline.js`:** eigener `scatter.js` (x/y/r-Geometrie) — Default eigener kleiner Renderer.
- **Menü-/Namensführung:** `/compare` (Cross-Run-Aggregat) vs. `/compare/{bundle}` (Modell/Varianten-Vergleich) klar benennen, damit der Nutzer beide unterscheidet.
- **Relations-Fazit-Schwellen:** ab welchem Δ ein Modell „lohnt" — rein deskriptiv halten (Zahlen nennen), keine harte Empfehlung verdrahten.
