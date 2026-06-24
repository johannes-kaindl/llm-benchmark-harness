# Modell-Auswahl in der GUI (Konfig + Start) — Design

**Datum:** 2026-06-21
**Status:** ratifiziert (Brainstorming abgeschlossen) — vor Implementierungsplan
**Scope:** Auf der „Konfig + Start"-Seite (`/config`) auswählen können, **welche Modelle** ein Eval-Lauf fährt — heute unmöglich, weil die Modelle im `models:`-Feld der gewählten `config*.yaml` verborgen sind und die Config nur als Ganzes gewählt wird.
**Auslöser:** Beim Bedienen der GUI fiel auf: man kann kein Modell wählen. Das ist die natürliche nächste Wand nach Ink. 8 (Modell-Vergleich) — um zwei Modelle zu *vergleichen*, muss man erst einen 2-Modell-Lauf *starten* können, was aus dem UI nicht ging (genau deshalb hatte `axis=model` kein echtes Bundle).
**Phase-Einordnung:** Erste, kleinste Stufe der Phase-2-„Variablen-Entkopplung / Steuerung brauchbar machen". Bewusst **ephemer** (kein Zurückschreiben in Configs) — die größere Entkopplung (Modelle/Settings/Prompts/Kriterien als eigene Bausteine, im Tool editieren) bleibt ein späterer eigener Schnitt.

## 1 · Ziel & Motivation

Das Tool ist Johannes' Labor: falsifizierbare Aussagen „Modell X auf HW Y mit Settings Z taugt für Aufgabe B" via Variablen-Kontrolle. Die Modell-Achse ist die erste Variable, die man bewusst variieren will (`dasselbe Pack mit zwei Modellen`). Der Vergleich (Ink. 8) zeigt das Ergebnis; diese Stufe liefert die **Eingabe**: aus dem UI gezielt die Modelle für einen Lauf zusammenstellen.

## 2 · Verifizierte Ausgangslage (Code-fundiert)

- **`/config`-Route** (`touchstone/gui/app.py`) rendert `config.html` mit zwei Dropdowns: **Pack** (`packs/*.yaml`) und **Config** (`config*.yaml`). **Kein Modell-Selektor.** (`judge_configs`, `eval_only_bundles` für die Judge-Form.)
- **Modelle** stehen in der Config-YAML: `Config.models: list[ModelSpec]` (`touchstone/config.py`). **`ModelSpec`:** `id: str`, `quant: str = ""`, `max_tokens_default: int = 400`.
- **Start-Pfad:** `/runs/eval`-POST → `RunRegistry.start_eval(*, pack_path, config_path, resume_dir=None)` (`touchstone/gui/control.py`) baut `argv = ["eval", "--pack", …, "--config", …, "--run-dir", …, "--emit-events", (--resume …)]` und spawnt `python -m touchstone eval …`.
- **`eval`-CLI** (`eval_cmd`, `touchstone/cli.py`): Optionen `--pack`, `--config/-c`, `--run-dir`, `--emit-events`, `--resume`, `--out`. Lädt die Config (inkl. `models`) und fährt die Matrix `for model in config.models` (`iter_eval_cells`). **Kein Modell-Override.**
- Stack: FastAPI + Jinja2 + Alpine + HTMX, **build-frei**. Bestehendes Muster: Daten server-seitig in die Vorlage einbetten, Alpine für Interaktivität.

## 3 · Ratifizierte Entscheidungen

| # | Entscheidung | Begründung |
|---|---|---|
| **M1** | **Picker auf der Start-Seite**: Checkboxen für die `models:` der gewählten Config (alle vorab an) **+ Ad-hoc-Zeile** (`id` + `quant`) für Modelle, die in keiner Config stehen. | Maximal flexibel; man kann sofort zwei beliebige Modelle gegen denselben Endpoint fahren und vergleichen. Stufe 1 der Entkopplung. |
| **M2** | **Ephemer, nur für diesen Lauf** — keine Config-Datei wird geschrieben. Das Bundle (`bundle.json`) protokolliert, was lief. | YAGNI; „im Tool editieren / zurückschreiben" ist ein späterer Schnitt. |
| **M3** | **Override ersetzt `config.models`** (kein Merge): die Picker-Auswahl IST die Modell-Liste des Laufs. | Vorhersagbar; „genau diese Modelle". Endpoint/seed/… bleiben aus der Config. |
| **M4** | **Ein Lauf = ein Endpoint** (der der gewählten Config). Kein Pool über Configs. | Jede Config hat ihren eigenen Endpoint; Pooling würde die Endpoint-Zuordnung mehrdeutig machen. |
| **M5** | **Transport als JSON** (`--models-json`), nicht colon-codiert. | Erhält alle `ModelSpec`-Felder (inkl. `max_tokens_default`) und ist Pydantic-validierbar. |
| **M6** | **Front-end: Server bettet Modelle aller Configs als JSON ein, Alpine tauscht die Liste** (Ansatz A). | Build-frei, kein neuer Endpoint, sofortige Reaktion; passt zum bestehenden Alpine-Daten-Muster. 6 kleine YAMLs pro Seitenaufruf zu parsen ist vernachlässigbar. |
| **M7** | **Bei `resume` ist der Picker aus.** | Resume fährt die festgelegten Zellen des Bundles erneut; Modelle liegen fest. |

## 4 · Architektur & Datenfluss

```
config.html (Alpine baut models_json aus Checkboxen + Ad-hoc)
  └─ POST /runs/eval (Feld models_json)
       └─ Route: models_json → list[ModelSpec] validieren (Pydantic)
            ├─ ungültig/leer (wenn Picker aktiv) → config.html mit Fehler, KEIN Spawn
            └─ gültig → start_eval(models=…)
                 └─ argv += ["--models-json", <json>]  →  python -m touchstone eval --models-json … --pack … --config …
                      └─ eval_cmd: models_json → list[ModelSpec] → ersetzt config.models → Lauf
                           └─ bundle.json protokolliert die tatsächlichen Modelle
                                → /result, /compare?axis=model können jetzt 2 Modelle zeigen
```

## 5 · Komponenten (Verantwortung · Schnittstelle · Abhängigkeit)

- **`touchstone/gui/configs.py` (neu, pur):**
  - `config_models(path: str | Path) -> list[ModelSpec]` — parst **nur** das `models:`-Feld einer `config*.yaml` (YAML laden, jedes Element via `ModelSpec(**m)` validieren). **Defensiv:** Datei fehlt / YAML kaputt / `models` fehlt oder ist kein Listenelement → `[]`. (Nutzt absichtlich nicht `load_config`, damit eine Config mit Platzhalter-Endpoint trotzdem ihre Modelle zeigt.)
  - `models_by_config(files: list[str]) -> dict[str, list[dict]]` — `{config_path: [model.model_dump(), …]}` für die Vorlage (JSON-serialisierbar).
- **`touchstone/gui/app.py`:**
  - `/config`-Route: zusätzlich `models_by_config(config_files)` an die Vorlage geben (Key `models_by_config`).
  - `/runs/eval`-POST: neues optionales Form-Feld `models_json: str = Form("")`. Bei `resume` wird der Override ignoriert. Sonst, wenn nicht-leer → `models_from_json(...)`; bei JSON-/Validierungs-/Leer-Fehler → **`HTTPException(400)`, kein Spawn** (codebase-konform: die Route liefert sonst JSON und nutzt `HTTPException`, z. B. 409 bei RunInProgress — daher 400 statt HTML-Re-render; der Front-end-Submit ist bei 0 Modellen ohnehin deaktiviert). Gültige Liste → `start_eval(..., models=specs)`.
- **`touchstone/gui/control.py` — `RunRegistry.start_eval`:** Signatur erweitern um `models: list[ModelSpec] | None = None`; falls gesetzt → `argv += ["--models-json", json.dumps([m.model_dump() for m in models])]`.
- **`touchstone/cli.py` — `eval_cmd`:** Option `models_json: str = typer.Option("", "--models-json", help="JSON list[ModelSpec]; replaces config.models for this run (GUI picker)")`. Wenn nicht-leer → parsen → `cfg.models = [ModelSpec(**m) …]` vor dem Lauf. (Helfer `_apply_models_override(cfg, models_json) -> Config` rein/testbar.)
- **`touchstone/gui/templates/config.html`:** Alpine-Block im „Eval starten"-Formular:
  - `x-data` hält `byConfig` (= eingebettetes `models_by_config` via `tojson`), `selected` (Config-Pfad, an `<select>` gebunden), `picked` (Set/Map der angehakten Modelle), `adhoc` (Liste `{id, quant}`).
  - Bei Config-Wechsel: `picked` = alle Modelle der neuen Config (vorab an).
  - Checkbox-Liste der Config-Modelle (`id` · `quant`), plus „+ Modell"-Zeile (zwei Inputs + Button → an `adhoc` anhängen, entfernbar).
  - Verstecktes `<input name="models_json">` (computed) = JSON aus `picked` ∪ `adhoc` (`{id, quant, max_tokens_default}`; Ad-hoc `max_tokens_default = 400`).
  - Submit deaktiviert, wenn 0 Modelle gewählt. **Bei `resume` der ganze Picker ausgeblendet** (kein `models_json` gesendet).

## 6 · Error-Handling

Genaue Semantik des `models_json`-Feldes (löst die „leer"-Mehrdeutigkeit auf):
- **`models_json` = leerer String / Feld fehlt** → **kein Override**, Lauf nutzt `config.models` wie heute. Deckt `resume` und Alt-/Nicht-GUI-Clients ab (Rückwärtskompat).
- **`models_json` = `"[]"`** (explizit leere Auswahl) → `HTTPException(400)`, **kein** Spawn. Der Front-end-Submit ist bei 0 Modellen deaktiviert (`:disabled="count() === 0"`), das 400 ist Defense-in-Depth für gecraftete Posts.
- **`models_json` = ungültiges JSON / Element mit fehlender oder leerer `id`** → `HTTPException(400)`, **kein** Spawn. (`models_from_json` lehnt leere/whitespace-`id` ab und dedupliziert nach `(id, quant)`.)
- **Kaputte Config beim Anzeigen:** `config_models` → `[]`; diese Config zeigt keine Modelle (defensiv), die Seite bleibt. Wird sie ohne Override gestartet, verhält sich der Lauf wie heute.
- **CLI `--models-json` ungültig:** klarer Fehler-Exit (kein halber Lauf).

## 7 · Teststrategie (TDD)

**Unit (pur):**
- `config_models`: parst Modelle aus einer echten/temporären Config; defensiv bei fehlender Datei, kaputtem YAML, fehlendem `models`. `models_by_config`: Map über mehrere Dateien, inkl. einer kaputten (→ `[]`).
- `_apply_models_override(cfg, models_json)`: ersetzt `cfg.models` (replace, kein Merge); leerer String → unverändert; ungültiges JSON / fehlende Pflichtfelder → Fehler.

**Integration / Verhalten (`TestClient` + argv-fangender FakeLauncher):**
- `/config` rendert den Picker: Checkboxen für die Modelle der (ersten/gewählten) Config, „+ Modell"-Zeile, verstecktes `models_json`-Feld, eingebettetes `models_by_config`-JSON. Bei `?resume=…` ist der Picker **aus**.
- `POST /runs/eval` mit gültigem `models_json` → `start_eval` wird mit den geparsten `models` aufgerufen / `argv` enthält `--models-json` (FakeLauncher fängt argv).
- `POST /runs/eval` mit ungültigem/leerem `models_json` → 200 mit Fehler-Re-render, **kein** Spawn.
- `start_eval(models=…)`: argv enthält `--models-json <json>`; ohne `models` → unverändert (kein Flag).

**Rückwärtskompat:** bestehende Eval-Starts ohne `models_json` unverändert; Kern ohne `[gui]` lauffähig; `eval_cmd` ohne `--models-json` unverändert.

**Manuell (am Ende):** GUI starten → `/config` → eine Config wählen → ihre Modelle erscheinen → eines abwählen, ein zweites ad-hoc eintippen → „Eval starten" → Lauf fährt genau die zwei Modelle → danach `/compare/<bundle>?axis=model` zeigt sie nebeneinander.

## 8 · Berührte / neue Dateien

| Datei | Änderung |
|---|---|
| `touchstone/gui/configs.py` | **neu** — `config_models` + `models_by_config` (pur) |
| `touchstone/gui/app.py` | `/config` reicht `models_by_config`; `/runs/eval` nimmt + validiert `models_json`, ruft `start_eval(models=…)` |
| `touchstone/gui/control.py` | `start_eval(..., models=None)` → `--models-json` in argv |
| `touchstone/cli.py` | `eval_cmd --models-json` → `_apply_models_override` ersetzt `config.models` |
| `touchstone/gui/templates/config.html` | Modell-Picker (Checkboxen + Ad-hoc + hidden `models_json`), bei `resume` aus |
| `tests/` | neue Tests je §7 |
| `AGENTS.md` | kurze Notiz: GUI-Modell-Override (`--models-json`, ephemer, ersetzt `config.models`) |

## 9 · Scope-Grenze (YAGNI)

**In diesem Schnitt:** Picker (Config-Modelle + Ad-hoc), ephemerer Override eines Laufs, Validierung/Fehler, `resume`-Ausblendung.

**Bewusst NICHT:** Zurückschreiben in `config*.yaml`; Editieren bestehender `quant`/`max_tokens`; Pool/Endpoint-übergreifende Auswahl; Modelle/Settings/Prompts/Kriterien als eigene editierbare Bausteine (= späterer Entkopplungs-Schnitt); Auto-Discovery verfügbarer Modelle vom Endpoint (`/v1/models`) — wäre eine schöne Ergänzung, aber eigener Schnitt.
