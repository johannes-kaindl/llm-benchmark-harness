# Modell-Dropdown via Endpoint-Discovery — Design

**Datum:** 2026-06-22
**Status:** ratifiziert (Brainstorming abgeschlossen) — vor Implementierungsplan
**Scope:** Im Modell-Picker („Konfig + Start") ein **Dropdown der tatsächlich am Endpoint verfügbaren Modelle** anbieten (Server fragt `/v1/models` der gewählten Config ab), aus dem man ein Modell wählt und der Lauf-Auswahl hinzufügt. Plus: den schlechten Default beheben (Picker startet nicht mehr auf der embed-Config).
**Auslöser:** Nach dem Modell-Picker ([`2026-06-21-modell-auswahl-gui-design.md`](2026-06-21-modell-auswahl-gui-design.md)) fiel beim Bedienen auf: der Picker zeigt nur das eine in der (default-gewählten) Config gelistete Modell — ein Embedding-Modell — und ein Wechsel geht nur per Ad-hoc-Tippen. Gewünscht: ein **Dropdown** echter, ladbarer Modelle.
**Phase-Einordnung:** Zweite Stufe von „Steuerung brauchbar machen / Variablen-Entkopplung". Die `/v1/models`-Auto-Discovery war im Picker-Spec als späterer Schnitt notiert — das ist er.

## 1 · Ziel & Motivation

Das Tool ist Johannes' Labor: „Modell X auf HW Y mit Settings Z taugt für Aufgabe B". Um X gezielt zu variieren, will man aus den **real verfügbaren** Modellen wählen — nicht raten, was am Endpoint geladen ist, und nicht jede id von Hand tippen. Der OpenAI-kompatible Endpoint (ollama `:11434`, LM Studio `:1234`) listet seine Modelle unter `/v1/models`; genau das wird die Quelle des Dropdowns.

## 2 · Verifizierte Ausgangslage (Code-fundiert)

- **`touchstone/client.py`** — `OpenAIStreamClient(base_url: str, api_key: str = "not-needed", *, engine=…, engine_version=…)` kapselt `self._client = OpenAI(base_url=…, api_key=…)`. Die OpenAI-SDK bietet `self._client.models.list().data` (jedes Element hat `.id`). Dies ist die **einzige engine-bewusste Datei** (Architektur-Regel) — die Modell-Liste gehört hierher.
- **`touchstone/config.py`** — `Endpoint(base_url: str, api_key: str = "not-needed")`; `Config.endpoint: Endpoint`; `load_config(path) -> Config`.
- **`touchstone/gui/configs.py`** — pure Picker-Quelle (`config_models`, `models_by_config`), importiert bereits `touchstone.config`.
- **`/config`-Route** (`app.py`) — `config_files = sorted(str(p) for p in Path(".").glob("config*.yaml"))`; reicht u. a. `configs`, `models_by_config` an `config.html`. Confine-Muster für cwd-Pfade: `_confine_cwd` (lehnt absolute/`..`-Pfade ab).
- **`model_picker.js`** — Alpine-Komponente `modelPicker(byConfig)`; Default-Config = `Object.keys(byConfig)[0]` (= alphabetisch erste = `config.embed.yaml`). **Lädt nicht-deferred** (vor Alpine).

## 3 · Ratifizierte Entscheidungen

| # | Entscheidung | Begründung |
|---|---|---|
| **D1** | **Quelle = Auto-Discovery vom Endpoint** (`/v1/models` der gewählten Config), server-seitig abgefragt. | Echte, ladbare Modelle; entspricht der Labor-Idee. Server-seitig vermeidet CORS und nutzt den vorhandenen Client. |
| **D2** | **Dropdown fügt der Auswahl hinzu** (eine angehakte/ad-hoc Zeile mit der gewählten `id`); Config-Checkboxen + Ad-hoc-Eingabe bleiben. | Additiv, klein, reuse der bestehenden Auswahl-Maschinerie. Dropdown skaliert für viele Modelle (ollama). |
| **D3** | **Kurzer Timeout (~3 s), nie blockierend, nie 500.** Endpoint offline/Timeout → leeres Dropdown + Hinweis, Ad-hoc bleibt. | Ein toter Endpoint darf das UI nicht hängen lassen oder die Seite brechen. |
| **D4** | **Default-Config-Fix:** `*embed*`/`*vlm*`-Configs in der Eval-Form **nach hinten** ordnen → Picker startet auf einer normalen Chat-Config. | Behebt die konkrete Beschwerde (embed-Modell als Default). Heuristik über den Dateinamen (Konvention im Repo). |
| **D5** | **`list_models()` in `client.py`** (engine-bewusst), Discovery-Helfer mit **injizierbarem Lister** in `configs.py`. | Hält die SDK-Berührung in der einen engine-Datei; DI macht die Discovery ohne Live-Endpoint testbar. |

## 4 · Architektur & Datenfluss

```
config <select> @change  ──►  model_picker.js: syncFromConfig()
   ├─ (sync) Config-Modelle als Checkboxen + Ad-hoc-Reset  (wie bisher)
   └─ (async) fetch('/endpoint-models?config='+config)
        └─ Route GET /endpoint-models  ──►  configs.discover_endpoint_models(config)
             └─ load_config → OpenAIStreamClient(endpoint, timeout=3s).list_models()
                  └─ {"models": [ids…], "error": null}   |   offline → {"models": [], "error": "…"}
   ◄── JSON ──┘
   → endpointModels füllt das Dropdown; Pick + „Hinzufügen" → ad-hoc Zeile {id, quant:""}
        → fließt (wie alle Auswahl) in das versteckte models_json → /runs/eval (unverändert)
```

## 5 · Komponenten (Verantwortung · Schnittstelle · Abhängigkeit)

- **`touchstone/client.py`** — neue Methode:
  - `list_models(self) -> list[str]`: `return [m.id for m in self._client.models.list().data]`.
  - `__init__` erhält optional `timeout: float | None = None` → `OpenAI(base_url=…, api_key=…, timeout=timeout)` (None = SDK-Default; bestehende Aufrufer unverändert).
- **`touchstone/gui/configs.py`** — neu:
  - `discover_endpoint_models(config_path: str | Path, *, lister: Callable[[], list[str]] | None = None) -> dict[str, Any]`: ohne `lister` baut der Default-Lister `load_config(config_path)` + `OpenAIStreamClient(cfg.endpoint.base_url, cfg.endpoint.api_key, timeout=3.0).list_models()`. **Fängt jede Exception** → `{"models": [], "error": f"Endpoint nicht erreichbar: {e}"}`; Erfolg → `{"models": lister(), "error": None}`. Dedupliziert/erhält Reihenfolge der ids.
  - `order_configs(paths: list[str]) -> list[str]`: stabil sortiert, `*embed*`/`*vlm*` (Dateiname, case-insensitive) ans Ende.
- **`touchstone/gui/app.py`**:
  - `/config`-Route: `config_files = configs_mod.order_configs([str(p) for p in Path(".").glob("config*.yaml")])` (statt nur `sorted`).
  - neue Route `@app.get("/endpoint-models")` `def endpoint_models(config: str) -> dict`: `_confine_cwd(config)` → `return configs_mod.discover_endpoint_models(config)`. Confine wirft 404 bei bösem Pfad; sonst immer 200 (Fehler im `error`-Feld).
- **`touchstone/gui/static/model_picker.js`** — Komponente erweitern: State `endpointModels: []`, `endpointError: ""`, `endpointLoading: false`, `endpointPick: ""`. `syncFromConfig()` ruft am Ende `fetchEndpointModels()` (async, fire-and-forget). `fetchEndpointModels()`: `fetch('/endpoint-models?config='+encodeURIComponent(this.config))` → `endpointModels`/`endpointError`; eigener `try/catch`. `addFromEndpoint()`: bei gewähltem `endpointPick` → `adhoc.push({id, quant:"", k:_nextK++})`, dann `endpointPick=""`.
- **`touchstone/gui/templates/config.html`** — im Modelle-Block (nur `{% if not resume %}`): ein `<select x-model="endpointPick">` mit `<template x-for="mid in endpointModels">`, ein „Hinzufügen"-Button (`@click="addFromEndpoint()"`, `:disabled="!endpointPick"`) und Status (`endpointLoading` / `endpointError`).

## 6 · Error-Handling

- **Endpoint offline / Timeout / 404 auf `/v1/models`:** `discover_endpoint_models` fängt alles → `{"models":[], "error":"Endpoint nicht erreichbar: …"}`; Route liefert **200**. Dropdown leer + Hinweis; Ad-hoc-Eingabe bleibt voll nutzbar; Start unbeeinträchtigt.
- **Kaputte/fehlende Config:** `load_config` wirft → vom `try/except` als `error` gefangen (kein Crash).
- **Böser `config`-Query-Pfad** (absolut/`..`): `_confine_cwd` → `HTTPException(404)`.
- **`fetch` schlägt fehl (Netzwerk/Browser):** JS-`catch` → `endpointError` gesetzt, Picker bleibt bedienbar.
- **Rückwärtskompat:** `OpenAIStreamClient`-Bestandsaufrufer (ohne `timeout`) unverändert; `/runs/eval`, `models_json`, Resume unverändert.

## 7 · Teststrategie (TDD)

**Unit (pur / DI):**
- `client.list_models`: gemockter `_client.models.list()` (Objekt mit `.data=[obj(id=…)]`) → Liste der ids. (Konstruktor mit/ohne `timeout`.)
- `discover_endpoint_models`: injizierter Lister → `{"models":[…],"error":None}`; Lister wirft → `{"models":[],"error": "…"}` (kein Throw). **DI-Lister deckt beide Zweige netzfrei** ab; der Default-Lister (echter Client) ist triviale Komposition und wird vom headless-Smoke gegen einen echten Endpoint belegt — **kein netzabhängiger Unit-Test** (vermeidet Flakiness/Timeouts in der Suite).
- `order_configs`: `["config.embed.yaml","config.m5.yaml","config.x.vlm.yaml","config.a.yaml"]` → `embed`/`vlm` hinten, Rest alphabetisch vorn.

**Integration / Verhalten (`TestClient`):**
- `GET /endpoint-models?config=config.m5.yaml` mit gemocktem/monkeygepatchtem `discover_endpoint_models` → JSON `{"models":[…]}`; Lister-Fehler → `{"models":[],"error":…}` mit **200**; `?config=../etc` → 404.
- `/config`: Default-Config ist nicht `config.embed.yaml` (Ordering greift); der Endpoint-Dropdown-Block (`endpointPick`, „Hinzufügen", `/endpoint-models`) ist im Markup; bei `resume` weiterhin aus.

**Headless-Browser-Smoke (Chrome `--dump-dom`, Pflicht — die letzte Lücke):** GUI starten, `/v1/models` mocken (oder gegen laufenden ollama), `/config` rendern und prüfen, dass das Dropdown sich füllt; per Mini-Interaktionsskript/`--dump-dom` belegen, dass die Komponente initialisiert (gerenderte `<option>`s). Mindestens: Alpine initialisiert den erweiterten `modelPicker` ohne JS-Fehler (gerenderte Checkbox wie beim letzten Fix).

**Manuell (am Ende):** GUI → `/config` → Default ist eine Chat-Config → Dropdown listet die ollama/LM-Studio-Modelle → eines wählen + „Hinzufügen" → erscheint als angehakte Zeile → „Eval starten" fährt es. Endpoint aus → Hinweis, Ad-hoc tippbar.

## 8 · Berührte / neue Dateien

| Datei | Änderung |
|---|---|
| `touchstone/client.py` | `list_models()` + optionaler `timeout` im Konstruktor |
| `touchstone/gui/configs.py` | `discover_endpoint_models` (DI-Lister) + `order_configs` |
| `touchstone/gui/app.py` | Route `GET /endpoint-models` (confined); `/config` nutzt `order_configs` |
| `touchstone/gui/static/model_picker.js` | Endpoint-Discovery-State + `fetchEndpointModels` + `addFromEndpoint` |
| `touchstone/gui/templates/config.html` | Endpoint-Dropdown + „Hinzufügen" + Status im Picker |
| `tests/` | Tests je §7 |
| `AGENTS.md` | Notiz: Endpoint-Modell-Discovery (`/endpoint-models`, `/v1/models`, Timeout, nie 500) |

## 9 · Scope-Grenze (YAGNI)

**In:** server-seitige `/v1/models`-Discovery der gewählten Config; Dropdown → Auswahl hinzufügen; Offline-/Fehler-Handling; Default-Config-Ordering.

**Bewusst NICHT:** Endpoint-Modelle vorab anhaken; Caching der Discovery; quant/Metadaten aus `/v1/models` ziehen (id genügt; quant bleibt Label); Multi-Endpoint-Pools; Discovery für die Judge-Form; Auto-Refresh-Polling.
