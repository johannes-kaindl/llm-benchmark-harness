# ADR-0001: OpenAI-kompatibel = einzige Schnittstelle

- **Status:** akzeptiert · 2026-06-28
- **Bereich:** Architektur

## Kontext

Der Harness soll *denselben Code* unverändert auf verschiedenen Maschinen fahren; nur die Config wechselt (Spec §1). Konkret laufen die Zielmaschinen auf unterschiedlichen Engines: LM Studio auf M1 und mlx_lm/mlx-openai-server auf M5 (design-decisions.md). Diese Engines sprechen alle das OpenAI-Chat-Protokoll. Die Kraft im Hintergrund ist die Messung selbst: Würde der Hot-Path je Engine verzweigen, hinge die gemessene Latenz vom Code-Zweig statt von der Hardware ab, und ein Maschinenwechsel wäre ein Code-Eingriff statt eines Config-Tauschs. Außerdem gilt als feste Harness-Regel, dass es keinen engine-spezifischen Code außerhalb von `client.py` geben darf (Spec §3, Non-goals).

## Entscheidung

Der gesamte Hot-Path kennt ausschließlich das OpenAI-Chat-Protokoll. Die einzige engine-bewusste Datei ist `touchstone/client.py`; sie ist bewusst dünn und adaptiert die Streaming-Chunks des OpenAI-SDK auf das engine-agnostische `StreamEvent`, das der Runner konsumiert (client.py Modul-Docstring). Dieselbe Client-Klasse `OpenAIStreamClient` dient sowohl der Generierung als auch dem Judging (Spec §4, „Reused, not duplicated"). Ein Maschinenwechsel ist damit ein Config-Tausch, kein Code-Zweig (design-decisions.md).

## Erwogene Alternativen

- **Engine-spezifischer Code im Hot-Path (Code-Zweig je Engine)** — verworfen, weil der Mess-Thread dann pro Engine unterschiedlich liefe und die Latenz nicht mehr sauber der Hardware zuzuordnen wäre; festgeschrieben als Non-goal „No engine-specific code outside `client.py`" (Spec §3).
- **Ein `/metrics`-Endpoint der Engine als Speicher-Quelle** — verworfen zugunsten des engine-agnostischen Host-Samplings als Quelle der Wahrheit für Speicher; ein optionaler `/metrics`-Endpoint wäre nur Beiwerk (design-decisions.md).

## Auswirkungen

- Positiv: Derselbe Code läuft unverändert auf LM Studio (M1) und mlx_lm/mlx-openai-server (M5); der Maschinenwechsel reduziert sich auf einen Config-Tausch (design-decisions.md), was die cross-machine vergleichbaren, publizierbaren Benchmarks erst ermöglicht (Spec §1).
- Positiv: Der Judge nutzt dieselbe OpenAI-kompatible Client-Infrastruktur (`client` als Judge-Endpoint), sodass ein starker Cloud-Judge oder ein lokaler Endpoint (Ollama/LM Studio) ohne neuen Code austauschbar ist (Spec §4, §7).
- Trade-off / Restgrenze: Engine-spezifische Fähigkeiten werden nur best-effort genutzt. Der Build-/Quant-Probe `probe_build_metadata()` funktioniert nur bei LM Studio (`/api/v0/models`), andere Engines liefern leere Metadaten und der Aufrufer fällt auf die Config zurück (client.py, `probe_build_metadata` / `parse_lmstudio_models`). Was nicht im OpenAI-Protokoll steht, bleibt außerhalb des Hot-Paths.
- Trade-off / Restgrenze: „Thinking"-Modelle legen ihr Reasoning auf nicht-standardisierte Felder (`reasoning_content` bzw. `reasoning`); `client.stream` fängt diese explizit ab, statt sie zu verwerfen (client.py) — eine bewusst engine-tolerante Sonderbehandlung am Rand des einen Protokolls.

## Belege & Links

- Spec: `docs/superpowers/specs/2026-06-19-ndeval-harness-design.md` · Code: `touchstone/client.py` · Rationale: `docs/explanation/design-decisions.md` · Tests: `tests/test_client.py`, `tests/test_client_models.py`, `tests/test_client_probe.py` (Konvention laut Spec §10: reine Logik unit-getestet, I/O per Dependency-Injection)
- Verwandt: keine offensichtlich verwandten ADRs in den gelesenen Quellen belegt
