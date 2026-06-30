# ADR-0013: Judge-Quality-Meta-Eval-Methode

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Judge

## Kontext

Der lokale LLM-as-judge (`touchstone judge`) vergibt Scores + Begründungen, aber **niemand prüft den
Judge selbst**. Beobachtet wurde ein systematischer Begründungs-Defekt (ein 4/5, das nie benennt, *was
besser* gewesen wäre). Ohne ein Maß für die Judge-Qualität bleibt zweierlei unbeantwortbar: (1) **welches
Modell der bessere Judge ist** und (2) **wie man den Judging-Prompt gezielt verbessert** — beides ist nötig,
damit die publizierten Quali-Scores vertrauenswürdig sind.

## Entscheidung

Eine **Export → externe Cloud-KI → Ingest**-Schleife (vom User so entschieden: kein API-Key/Kosten,
reproduzierbar, passt zur portablen MD-Export-Philosophie). `touchstone judge-meta export <bundle>` schreibt
`judge_meta_request.md` (geordnet: **Teil A** = blanker Frisch-Bewertungs-Auftrag *ohne* lokale Scores;
**Teil B** = die lokalen `dim_scores`+`dim_rationales` zur Kritik) plus eine leere, pydantic-validierte
`judge_meta_response.yaml`-Vorlage. Eine Cloud-KI füllt die Antwort; `touchstone judge-meta ingest` rechnet
daraus **Agreement/Kalibrierung** (Δ je Dimension, `mean_abs_delta`, Quality%-Δ, K.-o.-Konkordanz) + eine
**fixe Rubrik** (`cites_evidence`, `names_improvement`, `justifies_level`, `catches_safety`) und rendert
`judge_quality.md` mit einer vergleichbaren Headline. Reine Logik in `touchstone/gui/judge_meta.py`
(`compute_agreement`, `aggregate_rubric`, `render_request_md`, `render_judge_quality_md`); das Request-Dok
recycelt die in Sub-Projekt E aus `report_md` extrahierten Sektions-Helfer (`_eval_task`,
`section_master_scorecard`).

## Erwogene Alternativen

- **Direkter Cloud-API-Call (Key im Harness)** — verworfen: Kosten + nicht reproduzierbar; die manuelle
  Export/Ingest-Brücke passt zum portablen Bundle + zur realen Cloud-Nutzung (Claude).
- **Per-Antwort-Verdict-Meta-Eval** — verworfen für v1: die Master-Dimensionen leben auf der **holistischen**
  Ebene (`reports.jsonl`/`dim_scores`); die Meta-Eval prüft genau diese Ebene, nicht die per-Prompt-Verdicts.
- **Cross-Judge-Aggregat** (eine `/compare`-artige Judge-Tabelle) — bewusst **verschoben** (Follow-up); v1
  liefert nur die *vergleichbare Headline* je Bundle.
- **Automatisches Editieren des Judge-Prompts** — verworfen: F liefert Evidenz + Empfehlung; den Prompt
  ändert ein Mensch (geschah am 2026-06-28, siehe ADR-0012).

## Auswirkungen

- Positiv: eine **vergleichbare Headline** (mean|Δ| + Rubrik-Quoten) je Bundle erlaubt Judge-Modell-Ranking
  und gezielte Prompt-Verbesserungen. Empirisch belegt: die erste Anwendung über 6 Bundles (mit unbiased
  3-Scorer-Referenz) fand die Defekte, die ADR-0012 fixte (catches_safety buero 29 → 50 %).
- Trade-off / Restgrenze: Die Bias-Isolation ist in v1 „ein Pass, geschützte Reihenfolge" (Teil A vor Teil B);
  echter Voll-Rigor (zwei separate Pässe + Kritik-Panel) ist YAGNI für v1 und wurde bei der 2026-06-28-Anwendung
  manuell ergänzt. Der `cites_evidence`-Binär-Check unterscheidet Pointer von echtem Zitat **nicht** (zu lasch);
  `names_improvement` vermengt „Defekt benannt" mit „Fix benannt" — beides Kandidaten für ein judge-meta-v2.

## Belege & Links

- Spec: `docs/superpowers/specs/2026-06-26-judge-quality-meta-eval-design.md` · Code:
  `touchstone/gui/judge_meta.py`, `touchstone/cli.py` (`judge-meta export`/`ingest`) · Tests:
  `tests/test_judge_meta.py`, `tests/test_cli_judge_meta.py`
- Befunde + A/B der ersten Anwendung: `docs/explanation/judge-quality-meta-eval-2026-06-28.md`
- Verwandt: ADR-0007 (holistische Dimensionen), ADR-0012 (Judge-Prompt-Härtung)
