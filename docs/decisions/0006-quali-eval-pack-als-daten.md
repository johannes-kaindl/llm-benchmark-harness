# ADR-0006: Quali-Eval-Teilung (eval↔judge) + Pack = Daten

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Quali-Eval

## Kontext
Die qualitative Evaluation muss zwei grundverschiedene Use-Cases prüfen können — emotionale Stütze (`ndassist`, K.-o. = Sicherheit) und faktische Zuverlässigkeit (`buero`, K.-o. = keine Halluzination) — ohne für jeden Use-Case Code zu ändern (Spec, „reine Daten (YAML), kein Code"; `pack.py`-Docstring: „A pack is *data, not code*"). Zugleich erzeugt ein Lauf zwei klar trennbare Artefakte: das deterministische Generieren von Antworten und das anschließende Bewerten. `qualrun.py` beschreibt sich selbst als „The deterministic half of the eval"; `results.py` trennt explizit „Generation fills `EvalResponse`; judging fills the rest" (`Verdict`, `ModelReport`). Kräfte: Reproduzierbarkeit (deterministisches Sampling), Wiederaufnahme nach Abbruch (Generieren ist der teure, schützenswerte Teil), und ein Datenkontrakt, der zwischen Generieren und Bewerten stabil bleibt.

## Entscheidung
Die Quali-Eval wird in zwei Phasen über einen stabilen Datenkontrakt getrennt: (1) `run_eval` (`qualrun.py`) treibt die Matrix Modell × `prompt_variant` × Pack-Prompt × `repeat`, schreibt jede Antwort sofort inkrementell nach `responses.jsonl` als `EvalResponse` (`results.py`) und ist via `--resume` wiederaufnehmbar; (2) das Bewerten konsumiert diese `EvalResponse`-Records und füllt `Verdict`/`ModelReport`. Der Use-Case selbst ist reine Daten: ein `Pack` (YAML, geladen via `load_pack`) bündelt `scale`, gewichtete `dimensions`, `ko_rule`, `prompt_variants`, `sampling` (Default temperature 0.0, seed 42) und `categories[]`. Strenge Validierung beim Laden (`pack.py`: doppelte Prompt-IDs, unbekannte `ko_rule.dimension`, dangling `red_flag_prompts`, doppelte Variant-IDs) lehnt einen kaputten Pack ab, bevor ein einziger Request rausgeht.

## Erwogene Alternativen
- **Generieren und Bewerten in einem untrennbaren Schritt** — verworfen, weil dann ein Abbruch oder ein Judge-Fehler die teure Generierung verwirft; `qualrun.py` schützt explizit den teuren Teil („the expensive part — generation — is what must survive a crash"), indem Antworten inkrementell persistiert und beim Resume übersprungen werden.
- **Use-Case als Code statt als YAML-Daten** — verworfen, weil dann jeder neue Use-Case eine Code-Änderung bräuchte; `pack.py` macht das Austauschen der YAML zum einzigen nötigen Schritt („swapping the YAML moves the harness … with no code change").
- **Validierung erst zur Laufzeit / während des Laufs** — verworfen, weil ein dangling Dimension-Verweis oder eine doppelte Prompt-ID dann erst nach teurer Generierung aufflöge; `pack.py` validiert strikt vorab, „before a single request is sent".

## Auswirkungen
- Positiv: Ein zweites Pack (`buero`) validiert die Mechanik (Discovery, GUI-Listung, Cross-Judge-Aggregat nach Pack) ganz ohne Code-Änderung — schärfster Kontrast zu `ndassist` (Spec, „Ziel & Kontext").
- Positiv: Läufe sind reproduzierbar (deterministisches `sampling`, temp 0.0 + seed 42) und überleben Abbrüche (inkrementelles `responses.jsonl` + `--resume`, tolerant gegen eine halb geschriebene letzte Zeile, `load_responses_jsonl`).
- Positiv: Der Datenkontrakt (`EvalResponse`/`Verdict`/`ModelReport`) entkoppelt Generieren von Bewerten sauber (`results.py`).
- Trade-off / Restgrenze: Der Pack ist nur Daten — ein realer Eval-Lauf gegen ein echtes Modell ist bewusst kein Akzeptanzkriterium, sondern optionaler Folgeschritt (Spec, „Out of Scope").
- Trade-off / Restgrenze: Im Finalize-Pass werden Host-Ressourcen (RAM/Pressure/Throttle) nachträglich per Zeitfenster gemergt; vor dem Baseline-Tick erzeugte Bundles haben `sys_used_baseline_mb`/`sys_used_delta_mb` = None (`results.py`, `qualrun.py`-Finalize).

## Belege & Links
- Spec: `docs/superpowers/specs/2026-06-27-buero-pack-design.md` · Code: `touchstone/pack.py`, `touchstone/qualrun.py`, `touchstone/results.py` · Tests: `tests/test_pack.py` (Spec-Akzeptanzkriterium 2: `test_shipped_buero_pack_parses`)
- Verwandt: ADR-XXXX (K.-o.-`red_flag_scope`, vgl. `docs/superpowers/specs/2026-06-27-ko-red-flag-scope-design.md`)
