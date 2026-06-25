# Spec: Einzel-Modell-Picker für „Eval starten"

**Datum:** 2026-06-25 · **Branch:** `feat/gui-single-model-picker` · **Status:** approved

## Problem

Der heutige Modell-Picker auf der „Eval starten"-Seite (`macros/_config_eval.html`,
`static/model_picker.js`) ist verwirrend und messtechnisch riskant:

1. **Falsche Wahrheitsquelle als Default.** Vorausgewählt werden die in der Config *deklarierten*
   Modelle (`models_by_config`), nicht die auf dem Endpoint *real geladenen*. Default-Config ist
   `config.example.yaml` → der Platzhalter `qwen3-8b · Q5_K_M` ist vorgehakt, obwohl er evtl. gar
   nicht geladen (und der bloße String mehrdeutig: Qwen3 gibt es als Chat- *und* Embedding-8B) ist.
2. **Drei überlappende Wege für eine Aufgabe:** Checkboxen (aus Config) · „+ Modell" (Freitext) ·
   „Vom Endpoint hinzufügen" (Dropdown). Überladungs-Antipattern.
3. **Mehrfachauswahl ist für die Kern-Metrik des Tools (RAM) defekt.** Verifiziert (file:line,
   2026-06-25): ein Run *kann* mehrere Modelle fahren, aber RAM-Delta ist ab dem **zweiten** Modell
   unbrauchbar — geteilte Baseline (`sampler.py:245`) + nie ein Unload + selbe Baseline pro Fenster
   (`merge.py:84`). Hält der Endpoint das Vormodell resident (LM Studio JIT, Ollama `keep_alive`),
   bläht A's Residual-Gewicht das Delta von B. Siehe Memory `multimodel-ram-confound`.

## Ziel

Den Multi-Select-Picker durch eine **Einzel-Modell-Auswahl** ersetzen, gespeist aus den **real
geladenen** `/v1/models` des gewählten Config-Endpoints. Config-Knobs (quant, max_tokens_default,
reasoning_headroom_tokens, extra_body) bleiben erhalten, wenn das gewählte Modell config-deklariert
ist. Graceful Offline-Fallback + manueller Notausgang. **Backend unverändert:** der Picker emittiert
ein `models_json` mit **genau einem** Element → `models_from_json`/`start_eval`/RunRegistry bleiben
unangetastet und vorwärtskompatibel zur späteren sequenziellen Nacht-Queue.

## Nicht-Ziele

- Keine Nacht-/Batch-Queue (eigenes späteres Feature, eigener Brainstorm).
- Keine Änderung am Messlayer (sampler/merge/baseline).
- Keine Änderung an `/runs/eval`, `models_from_json`, `apply_models_override`, RunRegistry, Resume.
- Resume-Pfad unverändert (Resume überschreibt Modelle nie).

## Design

### Pure Logik — `touchstone/gui/configs.py`

Neue reine, unit-testbare Funktion:

```python
def eval_model_options(
    config_models: list[dict], endpoint_models: list[str]
) -> dict[str, Any]:
    """Merge config-declared models with the endpoint's actually-served ids into one
    single-select option list + a sensible default. Pure; no I/O."""
```

- Output: `{"options": [opt, ...], "default_id": str | None}`, jedes `opt` =
  `{id, quant, max_tokens_default, reasoning_headroom_tokens, extra_body, served: bool, source}`
  mit `source ∈ {"endpoint","config","both"}`.
- Regeln:
  - Für jede Endpoint-id (in Endpoint-Reihenfolge): `served=True`. Match in `config_models` →
    Config-Knobs übernehmen, `source="both"`. Sonst Defaults (quant="", max_tokens_default=400,
    reasoning_headroom_tokens=0, extra_body={}), `source="endpoint"`.
  - Config-Modelle, deren id **nicht** serviert wird: anhängen, `served=False`, `source="config"`,
    mit ihren Knobs.
  - `default_id` = id der ersten *served* Option; sonst erste Config-Option; sonst `None`.
  - id-Dedup (erste gewinnt), falls eine Config-id doppelt ist.

### Route (dünnes I/O) — `touchstone/gui/app.py`

```
GET /eval-model-options?config=<path>
  → guard config ∈ glob("config*.yaml"), sonst 404 (wie /endpoint-models)
  → disc = configs_mod.discover_endpoint_models(config)   # {models, error}, nie raise, 3s-Bound
  → opts = configs_mod.eval_model_options(
              [m.model_dump() for m in configs_mod.config_models(config)], disc["models"])
  → {"options": opts["options"], "default_id": opts["default_id"], "error": disc["error"]}
```
Nie 500 (toter Endpoint → `error` gesetzt, `options` = Config-Fallback).

### Template — `macros/_config_eval.html` (Nicht-Resume-Zweig ersetzen)

- **Endpoint-Statuszeile:** „Endpoint: N geladen ✓" / „⚠ Endpoint nicht erreichbar" — Form+Text
  redundant kodiert (Icon **und** Label, nicht nur Farbe; Johannes hat Rot-Grün-Schwäche).
- **Ein `<select>`** `x-model="chosen"`:
  - served-Optionen zuerst, Label `"<id> · <quant>"` (kein „· quant" wenn leer);
  - nicht-servierte Config-Optionen danach, Label `"<id> (nicht geladen)"`;
  - finale Option `__manual__` → „✎ andere Modell-ID…".
- `chosen === '__manual__'` → Textfeld `manualId` (+ optional `manualQuant`) einblenden.
- Hidden `name="models_json"` `:value="modelsJson()"` — **Ein-Element-Array** mit vollen Knobs.
- Submit `:disabled="count() === 0"`; Hinweis „Wähle ein Modell." wenn 0.
- Default-Auswahl = `default_id` (bei Load + Config-Wechsel gesetzt).

### JS — `static/model_picker.js` (`modelPicker` vereinfachen)

- **Entfernen:** Checkbox-Liste, `adhoc`-Array, Endpoint-Dropdown, `addFromEndpoint/addAdhoc/removeAdhoc`.
- **State:** `config, options, chosen, manualId, manualQuant, endpointError, endpointLoading, defaultId`.
- `init`/`syncFromConfig` → `fetch('/eval-model-options?config=' + …)`; `options` + `chosen=default_id`.
  Stale-Response-Guard (überholte Config ignorieren) wie heute beibehalten. Initial-Seed aus
  `models_by_config[config]` (served=false) gegen Flash/Offline, dann von der Route überschrieben.
- `modelsJson()`:
  - `chosen === '__manual__'` → `manualId.trim()` ? `[{id, quant: manualQuant.trim(), max_tokens_default:400}]` : `[]`
  - sonst Option per id finden → `[{id, quant, max_tokens_default, reasoning_headroom_tokens, extra_body}]`
  - sonst `[]`
- `count()` → 1 wenn ein Modell auflösbar, sonst 0.

## Tests (TDD)

**Pure `eval_model_options`** (`tests/test_gui_configs.py` o. ä.):
- Endpoint hat Modelle, keins in Config → alle served, source endpoint, Defaults, default_id = erstes Endpoint.
- Endpoint-Modell auch in Config → source both, Config-Knobs übernommen (quant/reasoning_headroom/extra_body/max_tokens_default).
- Config-Modell nicht serviert → angehängt, served false, source config.
- Endpoint leer/offline → options = Config-Modelle served false, default_id = erstes Config oder None.
- Beide leer → options [], default_id None.
- Reihenfolge: Endpoint-Reihenfolge erhalten, Config-only danach; id-Dedup.

**Route `/eval-model-options`** (TestClient, `discover_endpoint_models` gemockt/`lister` injiziert):
- Happy: `{options, default_id, error:null}`-Shape.
- Endpoint-Error wird durchgereicht, nie 500.
- Pfad-Guard: config ∉ glob → 404.

**Bestehende Tests:** `test_gui_config_flow.py` an den neuen Picker-Kontrakt anpassen
(alte Checkbox/Adhoc-Assertions). `start_eval`/`models_json`-Tests bleiben grün (Backend unverändert;
Ein-Element-Array validiert).

**Headless-Smoke** (Memory `gui-restart-after-changes`): Server neu starten, `/config` lädt 200 mit
neuem Picker-Markup; `/eval-model-options?config=…` antwortet im erwarteten Shape.

## Berührte Dateien

- `touchstone/gui/configs.py` — `eval_model_options` (pure).
- `touchstone/gui/app.py` — `GET /eval-model-options`.
- `touchstone/gui/static/model_picker.js` — `modelPicker` → Einzelauswahl.
- `touchstone/gui/templates/macros/_config_eval.html` — Modell-Block ersetzen.
- `tests/` — Unit-Tests `eval_model_options` + Route; bestehende Config-Flow-Tests anpassen.

## Risiken / Edge-Cases

- Endpoint-Latenz (3s) — wie heute async, „lädt…"-State.
- Config mit mehreren deklarierten Modellen (keine mitgeliefert, aber möglich) → alle als Optionen,
  Nutzer wählt eins (kein Auto-Run-aller mehr — beabsichtigt).
- Manuelle id ohne Endpoint → funktioniert (Notausgang).
- `models_json` Ein-Element-Array → von `models_from_json` sauber dedupt.
