---
paths:
  - "touchstone/gui/**"
  - "tests/test_gui_*.py"
  - "tests/test_report_md_golden.py"
  - "tests/golden/**"
---

- **The GUI (`touchstone gui`) is an optional `[gui]` extra and never measures in-process.** The
  long-lived FastAPI server (`touchstone/gui/`, deps isolated in the `[gui]` extra — the core never
  imports it) is an **out-of-process control-plane**: it spawns `python -m touchstone eval/judge`
  with `--run-dir <host-chosen>` `--emit-events` (event-writers without the webmon monitor — the GUI
  tails `events.jsonl` itself) and reuses the pure read layer (`load_pack`, `scorecard.master_rows`,
  `aggregate`, `tail`+`build_view`). `runs/` stays the SSOT; the GUI's only state is a **transient
  run-sentinel** (`run.json` in the active run dir) that triples as run_dir handle, cross-process
  **one-run lock** (survives a GUI restart; only one measurement at a time = mess-cleanliness), and
  discovery anchor for running/crashed runs. Sentinel + event files are transient, **excluded from
  discovery and the `/export` allowlist**. Status + verdict are **derived/recomputed** per bundle
  (not in `bundle.json`). `serve()` binds 127.0.0.1 only; TrustedHost + Origin checks guard the
  state-changing POSTs against DNS-rebinding/CSRF.
- **`reports.jsonl` is the `ModelReport` persistence** (one line per (model, variant): `dim_scores`
  + `dim_rationales`), written by `judge` in BOTH paths at finalize (`_render_judge_scorecard`). It is
  the **sole** source of per-dimension rationales (`scores.csv` has none); the GUI reads it first and
  falls back to the lossy `scores.csv` reconstruction (then „Begründung nicht erfasst"). Master
  dimensions are scored **holistically** (one judge call over all answers) — so traceability runs
  through the judge's rationale **citing prompt_ids** (clickable in the result view), not through a
  dimension→prompt structure (none exists). The evaluation method is explained in
  `docs/explanation/design-decisions.md` (+ ADR `docs/decisions/0007-holistische-dimensionen.md`) and
  surfaced UI-retrievably via `templates/_method_explainer.html`.
- **`resources.jsonl` ticks carry `cpu_pct`** (system CPU %, `None` for pre-cpu ticks) — additive,
  does not touch `RAW_CSV_COLUMNS`. The result view plots RAM + CPU over the run.
- **GUI-Modell-Override (ephemer):** Die „Konfig + Start"-Seite zeigt die `models:` der gewählten
  Config als Checkboxen + eine Ad-hoc-Zeile (id/quant). Die Auswahl geht als `models_json` an
  `/runs/eval` → `RunRegistry.start_eval(models=…)` → `eval --models-json` → `apply_models_override`
  **ersetzt** `config.models` nur für diesen Lauf (Endpoint/seed bleiben aus der Config; nichts wird
  in die Config zurückgeschrieben — das Bundle protokolliert, was lief). Leeres/ungültiges
  `models_json` → 400, kein Spawn; `resume` ignoriert den Override. Pure Logik:
  `config.models_from_json`/`apply_models_override`, `gui/configs.py`.
- **Endpoint-Modell-Discovery:** Der Picker fragt beim Config-Wechsel `GET /endpoint-models?config=…`
  ab; der Server ruft `/v1/models` des Config-Endpoints (`OpenAIStreamClient.list_models`, Timeout 3 s
  **+ `max_retries=0`** → ~3 s harter Bound, sonst dehnen SDK-Retries einen toten Endpoint auf ~10 s)
  und liefert `{"models": [...], "error": null}` — **nie 500**, ein toter Endpoint ergibt `error` +
  leere Liste. Die Route lässt nur die angebotenen `config*.yaml` zu (kein Lesen beliebiger cwd-YAML).
  Das Dropdown füllt sich daraus; „Hinzufügen" legt eine Auswahl-Zeile an (dedupt per `id` gegen schon
  Gewähltes). Default-Config ordnet `*embed*`/`*vlm*` nach hinten (`order_configs`); der Picker-Default
  kommt aus der geordneten `configs`-Liste (`configs[0]`), nicht aus der JSON-Key-Reihenfolge.
  Pure Logik: `configs.discover_endpoint_models`. **`OpenAIStreamClient`-Timeout/`max_retries` nur
  forwarden, wenn gesetzt** — sonst überschriebe `timeout=None` den 600s-SDK-Default für alle eval/judge-Läufe.
- **Zwei Vergleichs-Ebenen, klar getrennt:** `/compare` (Station 6) ist das **Cross-Run-Aggregat**
  über *alle* Bundles (`aggregate.load_all_scores` → Tabelle Hardware×Qualität). `/compare/{bundle}`
  ist der **Innerhalb-Bundle-Achsen-Vergleich** (Ink. 8): eine kontrollierte Ansicht entlang `model`
  oder `variant` *innerhalb eines* Bundles — Effizienz-Scatter (x=Decode, y=Qualität, r=Peak-RAM
  `sys_used_mb`), Kopf-an-Kopf (`master_rows`⨝`reports`-Join; `master_rows` trägt kein `dim_scores`),
  per-Aufgabe-Drill-down (per-Prompt `Verdict.score`-Δ, V7 — orthogonal zur holistischen %).
  CPU wird GUI-seitig aus `resources.jsonl`-Fenstern berechnet (`compare._cpu_for_window`); da
  `cpu_pct` erst mit Ink. 7 kam und kein Bundle seither neu lief, ist CPU heute überall **„n. v."**
  (ein first-class getesteter Zustand). Pure Logik in `touchstone/gui/compare.py`.
- **Meta-Report (`/export-meta-report`) muss `render_report_md`s judging=0-Strip selbst re-applizieren.**
  `render_report_md` nullt verdicts/reports/master_rows **zentral** bei `include_judging=False` (ein Ort).
  Die daraus extrahierten `report_md.section_*`-Helfer sind aber **nicht** judging-blind —
  `section_prompts_antworten` rendert `· Judge n/5` + `**Judge:**`-Badge, sobald ein Verdict vorliegt.
  `gui/meta_report.py` reicht daher unter judging=0 `verdicts=[]` durch (`_bundle_detail_section`) und ruft
  `_eval_task` statt `section_master_scorecard`; sonst leaken Judge-Scores in den „Bewertungs-Auftrag" und
  verankern die Re-Judge-Cloud-KI (Sub-Projekt F). **Wer einen weiteren Composer über die `section_*`-Helfer
  baut, muss denselben Strip re-applizieren.** Die Bytegleichheit der Zerlegung sichert ein Golden-Test
  (`tests/test_report_md_golden.py`); `pool_rows` folgt **keinen** Symlinks → ein extern hineingelinktes
  Bundle erscheint nicht im `/compare`-Pool (Confinement sicher-by-construction, `is_relative_to`-Guard =
  Defense-in-Depth).
