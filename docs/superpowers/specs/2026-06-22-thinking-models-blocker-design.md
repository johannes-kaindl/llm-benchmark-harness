# Design: Thinking-Modelle → leerer Content (Harness-Blocker)

**Datum:** 2026-06-22
**Status:** Spec — zur Review
**Branch:** `feat/thinking-models-blocker`

## 1. Problem & Beleg

Erster echter Qual-Lauf via GUI (`google/gemma-4-12b-qat`, LM Studio `:1234`):
**50/52 Antworten `content_empty`**. Das Modell schrieb alles ins `reasoning`-Feld
(`delta.reasoning_content`); das ~400-Token-`max_tokens`-Budget war vom Reasoning
aufgebraucht, bevor je sichtbarer `content` kam. Der Judge bewertete jede leere
Antwort hart mit 1/5 → eine Scorecard aus lauter 1en.

**Das ist ein Harness-Bug, nicht das Modell.** Drei Lücken im Code:

1. **Keine Frühwarnung.** `run_eval` (`qualrun.py:122`) startet die volle Matrix ohne
   jeden Vorabtest. Ein Modell, das nur Reasoning ausgibt, fällt erst nach ~50 Cells auf
   (~30 Min). `cli.py:540` ruft `run_eval` ohne Pre-Flight; `gui/control.py:184` spawnt
   den Subprocess ohne Pre-Flight.
2. **Stille Fehlbewertung.** `judge.py:192-200` verdrahtet `content_empty → score=1,
   unscored=False` ohne Judge-Call. reasoning-only (Token-Starvation, *unser* Setup-Fehler)
   und echt-leer (Modell gibt wirklich nichts aus) werden zusammengeworfen. `reasoning_chars`
   wird zwar gemessen (`qualrun.py:170`), aber **nirgends** im Scoring genutzt.
3. **Kein Hebel.** `client.py:60` reicht **kein** `extra_body` durch; `ModelSpec`
   (`config.py:34`) hat keinen Thinking-/Budget-Knopf. Es gibt keine Möglichkeit, einem
   Reasoning-Modell mehr Denk-Budget zu geben oder Thinking abzuschalten.

Nicht-Bug, geprüft: Die TTFT-Messung ist **korrekt** für Reasoning-Modelle —
`runner.py:123-125` setzt `ttft` nur auf den ersten *sichtbaren* `delta_text`; Reasoning
blockt sie nie, bei reiner Reasoning-Ausgabe bleibt `ttft=nan` (nan-sicher in
`derive_rates`). Kein vierter Bug; nicht im Scope.

## 2. Ziele / Nicht-Ziele

**Ziele**
- Reasoning-Modelle **fair** vermessbar machen (faire Chance auf sichtbaren Content) **und**
- den Trade-off **transparent** machen, statt ihn still als 1/5 zu verbuchen.
- Verschwendete Voll-Läufe per Frühwarnung verhindern.
- Judge-Modell wie das Eval-Modell aus dem GUI wählbar machen.

**Nicht-Ziele / bewusste Auslassungen (YAGNI)**
- Kein automatisches Aufblähen des Budgets. Ein kleines `max_tokens` ist ein **legitimes
  Test-Setup** (kleine Maschine) — der Harness ändert die Mess-Bedingung nie ohne explizite
  Opt-in-Angabe.
- Kein engine-spezifischer Code außerhalb `client.py`. Der Thinking-Schalter ist generisches
  `extra_body`, kein `if engine == "lm-studio"`.
- Keine i18n der Rationale-Texte (Deutsch bleibt, wie der Rest der Judge-Texte).
- Kein eigener nicht-streaming Smoke-Pfad — der Pre-Flight nutzt denselben `stream_once`
  wie die Messung (eine Code-Bahn, gleiche Bedingung).

## 3. Übergreifende Prinzipien

- **Mess-Bedingung nie heimlich verfälschen.** Alle „faire-Chance"-Hebel sind opt-in,
  Default-neutral. Wer nichts setzt, sieht exakt das heutige Verhalten — nur transparenter.
- **Sichtbares Antwort-Budget vom Denk-Budget trennen.** `pack.prompt.max_tokens` bleibt das
  *gemeinte* sichtbare Budget (gleich für alle Modelle → fairer Vergleich). Reasoning-Headroom
  ist ein **separater, pro-Modell** Aufschlag aufs Gesamt-Budget.
- **DRY über CLI und GUI.** Die GUI spawnt `python -m ramcheck eval` als Subprocess; jede
  Logik in `run_eval` deckt damit beide Pfade ab. Surfacing läuft über die bestehende
  `events.jsonl`-Maschinerie, die die GUI ohnehin tailt.
- **Engine-agnostisch.** `extra_body` wird roh an die OpenAI-API durchgereicht; der Harness
  weiß nicht, *wie* eine Engine Thinking abschaltet — der User gibt es an (Doku liefert
  Beispiele).

## 4. Komponente ① — Pre-Flight-Smoke

### Zweck
Vor dem Voll-Lauf **einmal pro Modell** prüfen, ob das gewählte Setup (Modell × Endpoint ×
effektives Budget × extra_body) *sichtbaren Content* liefert. Frühwarn-Gate, keine Messung.

### Schnittstelle (pure, DI-testbar)
```
@dataclass
class PreflightResult:
    model: str
    status: Literal["ok", "reasoning_only", "empty", "not_loaded", "error"]
    text_chars: int
    reasoning_chars: int
    detail: str            # menschenlesbar (z.B. "nur Reasoning: 1423 chars, kein content")

def preflight_models(
    client: StreamClient,
    models: list[ModelSpec],
    *,
    prompt: str = SMOKE_PROMPT,
    budget_for: Callable[[ModelSpec], int],   # effektives max_tokens je Modell (s.u.)
) -> list[PreflightResult]
```
- Nutzt `stream_once` (gleiche Bahn wie die Messung), **ein** kleiner aber realistischer Prompt
  (`SMOKE_PROMPT`, z.B. „Antworte in genau einem Satz: Was ist 2 + 2?").
- **Smoke-Budget je Modell = `max(prompt.max_tokens über den Pack) + model.reasoning_headroom_tokens`**
  — also das **großzügigste** realistische Gesamt-Budget, *nicht* ein winziger Fixwert (sonst zeigte
  jeder Thinking-Smoke fälschlich „kaputt"). Bewusste Wahl: Der Smoke ist eine **gnädige** Frühwarnung
  — liefert das Modell selbst beim größten Budget nur Reasoning, ist die Warnung sicher korrekt; enge
  *kleinere* Budgets, die nur einzelne Cells reasoning-only machen, fängt die per-Cell-`unscored`-Logik
  (Komponente ②). So minimiert der Smoke fälschliche „kaputt"-Warnungen.
- Klassifikation: `not ok` → `error`/`not_loaded` (Exception-Text unterscheidet „model not
  found" von Transport); `ok` & `text.strip()` → `ok`; `ok` & leer & `reasoning_chars>0` →
  `reasoning_only`; `ok` & leer & kein reasoning → `empty`.
- Wirft **nie** selbst (defensiv wie `discover_endpoint_models`); ein toter Endpoint ergibt
  `error`-Results, kein Crash.

### Integration
- In `run_eval`, nach `cells = iter_eval_cells(...)` (`qualrun.py:111`), **vor**
  `sampler.start()` (damit der Smoke nicht im Ressourcen-Fenster der Messung liegt).
- Neuer optionaler Callback `on_preflight(results: list[PreflightResult])` (analog zu den
  vorhandenen `on_run_start`/`on_cell_*`). Der Event-Writer (`--emit-events`) schreibt eine
  `preflight`-Event-Zeile nach `events.jsonl`; die GUI zeigt sie in der Live-Karte.
- **Resume überspringt den Pre-Flight** (`resume=True` → Cells laufen weiter, kein Smoke).
- **Verhalten bei `reasoning_only`/`empty`/`error`:**
  - Default: **warnen** — CLI druckt eine prominente Warnung, Event wird geschrieben, Lauf
    startet. (Begründung: ein bewusst kleines Budget ist legitim; ein Hard-Abort würde genau
    dieses Szenario unmessbar machen. Ein falsch-negativer Smoke darf keinen validen Lauf
    verhindern.)
  - `--strict-preflight` (CLI-Flag, GUI-Checkbox später): bei nicht-`ok`-Modellen
    `RuntimeError` vor der Matrix → kein Lauf, kein run-dir-Müll.

### CLI / GUI
- `cli.py eval_cmd`: neues Flag `--strict-preflight/--no-strict-preflight` (Default off),
  durchgereicht an `run_eval`.
- `gui/control.py start_eval`: Pre-Flight läuft automatisch im gespawnten eval-Subprocess;
  die GUI rendert die `preflight`-Events in der bestehenden Live-Progress-Karte.

## 5. Komponente ② — Thinking-Handling

### 5a. Faire Chance (opt-in, an `ModelSpec`)
```
class ModelSpec(BaseModel):
    id: str
    quant: str = ""
    max_tokens_default: int = 400
    reasoning_headroom_tokens: int = 0          # NEU: extra Gesamt-Budget NUR für dies Modell
    extra_body: dict[str, object] = Field(default_factory=dict)   # NEU: generisch durchgereicht
```
- **Effektives Budget** in der Eval-Loop (`qualrun.py:131`):
  `max_tokens = cell.prompt.max_tokens + cell.model.reasoning_headroom_tokens`.
  Default `0` → identisch zu heute. Für gemma setzt der User z.B. `reasoning_headroom_tokens:
  2000` → Gesamt-Budget 2400, sichtbares Antwort-Budget bleibt konzeptionell 400.
  (Ehrlich dokumentiert: Headroom ist *Platz zum Denken*, keine harte Garantie auf sichtbaren
  Content — die OpenAI-API hat nur **ein** `max_tokens`.)
- **`extra_body`** wird `qualrun → stream_once → client.stream → chat.completions.create(
  extra_body=…)` durchgereicht (nur wenn nicht leer). Damit kann der User Thinking abschalten,
  wo der Endpoint es kann (z.B. `{"chat_template_kwargs": {"enable_thinking": false}}` bei
  qwen3-Stil) — ohne engine-Zweig im Harness.
- Schema-Erweiterungen sind additiv; `models_from_json` (GUI-Override) akzeptiert die neuen
  Felder mit Defaults (kein Picker-UI dafür in dieser Iteration — YAML/Config-Sache).

### 5b. Bewertung (`judge.py score_response`)
Den `content_empty`-Zweig (`judge.py:192-200`) aufteilen über `resp.reasoning_chars`:
- **reasoning-only** (`content_empty` & `reasoning_chars > 0`): `unscored=True`,
  `red_flag=False`, klare Rationale „reasoning-only: kein sichtbarer Content (N reasoning-chars)
  — Budget zu klein oder Thinking aktiv; nicht als Assistenz-Antwort bewertbar". **Kein Score
  von 1.** Fließt nicht ins Mittel (`scorecard.mean_score` schließt `unscored` aus).
- **echt-leer** (`content_empty` & `reasoning_chars == 0`): unverändert `score=1`,
  `unscored=False` — ein Modell, das wirklich nichts ausgibt, ist als Assistent unbrauchbar.
- `not ok` (Generierungsfehler): unverändert `score=0, unscored=True`.

`EMPTY_CONTENT_RATIONALE` (`judge.py:30`) wird in zwei Konstanten gesplittet
(`REASONING_ONLY_RATIONALE`, `EMPTY_RATIONALE`).

### 5c. Reasoning sichtbar machen
- `EvalResponse` bekommt `reasoning_text: str = ""` (neben dem schon vorhandenen
  `reasoning_chars`). **Befüllt nur bei `content_empty`** (`qualrun.py`), sonst leer — das hält
  `responses.jsonl` schlank (normale Antworten brauchen ihr Reasoning nicht).
- So ist das Denken in der GUI-Antwortansicht / `responses.jsonl` einsehbar und der reasoning-only
  Zustand ist nachvollziehbar (deckt deinen „Reasoning ggf. kommentieren"-Wunsch ab, ohne pro
  Cell einen teuren Judge-Call zu erzwingen).

### 5d. Transparenz in der Scorecard
- `scorecard.py` zeigt je (Modell, Variant) die **unscored-Quote mit Grund**
  (z.B. „38/52 reasoning-only · 2 Fehler") — damit ein Mittel, das auf wenigen bewerteten
  Antworten beruht, nicht fälschlich „vollständig" aussieht.
- Master-Dimensionen (`_build_dimension_prompt`) sehen ohnehin nur `not unscored`-Verdicts
  (`judge.py:154`) — reasoning-only zieht das holistische Urteil also nicht mehr nach unten.

## 6. Komponente ③ — Judge-Modell-Picker

### Discovery generisch machen
- `gui/configs.py`: Kern-Helper `discover_models(base_url, api_key) -> {"models": [...],
  "error": str|None}` (3 s Timeout, `max_retries=0` — wie heute). `discover_endpoint_models(
  config_path)` und neu `discover_judge_endpoint_models(judge_config_path)` sind dünne Wrapper,
  die ihre jeweilige Config laden und die Endpoint-Koordinaten extrahieren (`JudgeConfig.endpoint`).

### Route
- `gui/app.py`: neue Route `GET /judge-endpoint-models?judge_config=<path>`, **never-500**,
  gleicher Path-Traversal-Guard wie `/endpoint-models` (nur `judge*.yaml` aus dem cwd erlaubt).

### CLI-Override
- `cli.py judge`: neues optionales `--judge-model <id>`. Gesetzt → `jc.model_copy(update={
  "model": judge_model})` überschreibt das YAML-Modell für diesen Lauf (Muster wie eval
  `apply_models_override`). Bundle/YAML werden nicht zurückgeschrieben.

### GUI
- `gui/control.py start_judge`: optionaler Param `judge_model`; gesetzt → `["--judge-model",
  judge_model]` ans argv (Muster wie `start_eval` mit `--models-json`).
- `gui/app.py POST /runs/judge`: optionales Form-Feld `judge_model` → an `start_judge`.
- `templates/config.html` Judge-Form: ein Modell-Dropdown, das bei Wechsel des
  `judge_config_path` `/judge-endpoint-models` abfragt (analog zum Eval-`@change`).
- `static/model_picker.js`: schlanke Alpine-Komponente `judgeModelPicker()` — füllt **ein**
  Dropdown und schreibt in ein Hidden-Field. Kein quant/Multi-Select (Judge braucht nur `id`).
  *Verworfen:* die volle eval-`modelPicker`-Komponente wiederverwenden (Multi-Select/quant/adhoc
  sind für den Judge unnötiger Ballast).

### Wichtig
- Der **Judge selbst soll denken dürfen** — der Thinking-Aus-Schalter (Komponente ②) gilt **nur**
  für Eval-Modelle, **nie** für den `OpenAIJudgeBackend`.

## 7. Schema-Änderungen (Zusammenfassung)
- `ModelSpec`: `+ reasoning_headroom_tokens: int = 0`, `+ extra_body: dict = {}`.
- `EvalResponse`: `+ reasoning_text: str = ""` (nur bei `content_empty` befüllt).
- `runner.StreamClient`-Protocol + `stream_once`: `+ extra_body: dict | None = None` durchreichen.
- `client.OpenAIStreamClient.stream`: `+ extra_body` → `create(extra_body=…)` (nur wenn gesetzt).
- `judge.py`: `EMPTY_CONTENT_RATIONALE` → `REASONING_ONLY_RATIONALE` + `EMPTY_RATIONALE`.
- Keine Änderung an `RAW_CSV_COLUMNS` (die Perf-CSV ist unberührt; alles Additive liegt im
  Qual-Kontrakt `results.py` bzw. ist transient).

## 8. Datenfluss (neu)
```
config.yaml (ModelSpec.reasoning_headroom_tokens, extra_body)
   │
   ├─► run_eval ──► preflight_models() ──► on_preflight ──► events.jsonl ──► GUI-Karte / CLI-Warnung
   │                     (vor sampler.start; resume skippt)
   │
   └─► Matrix-Loop ──► stream_once(max_tokens = prompt + headroom, extra_body) ──► client.stream
                          │
                          └─► EvalResponse(content_empty, reasoning_chars, reasoning_text*)
                                  │  (*nur bei content_empty)
                                  └─► judge.score_response
                                         ├─ reasoning-only → unscored + Rationale
                                         ├─ echt-leer      → score 1
                                         └─ sonst          → Judge-Call
                                              └─► scorecard (unscored-Quote sichtbar)
```

## 9. Fehlerbehandlung
- Pre-Flight defensiv (wirft nie); tote Endpoints → `error`-Result + Warnung, kein Crash.
- `--strict-preflight` ist die **einzige** Stelle, die wegen Pre-Flight bewusst abbricht
  (`RuntimeError` vor der Matrix, vor jeglichem Schreiben).
- `extra_body`-Durchreichung ist optional; ein Endpoint, der ein Feld ignoriert, ist unkritisch.
  Ein Endpoint, der bei unbekanntem `extra_body` 4xx wirft, schlägt schon im Pre-Flight auf →
  Warnung, bevor die Matrix läuft.
- `judge --judge-model` mit nicht existierendem Modell → Judge-Call schlägt fehl → bestehender
  `unscored`-Pfad (`score=0, unscored=True`), kein Crash.

## 10. Test-Strategie (TDD, pytest)
Pure Logik unit-getestet, I/O dependency-injected (bestehendes Muster).
- **config/pack:** `ModelSpec` Defaults + `reasoning_headroom_tokens`/`extra_body`-Roundtrip
  durch `models_from_json`; effektives Budget = `prompt.max_tokens + headroom`.
- **client:** `extra_body` landet (nur wenn gesetzt) in `create(...)` (Fake-SDK-Spy); leer →
  Argument gar nicht gesetzt (kein `None`-Override des SDK-Defaults).
- **runner:** `stream_once` reicht `extra_body` durch.
- **preflight:** Fake-Client liefert content / reasoning-only / leer / Exception → korrekte
  `status`-Klassifikation; `on_preflight`-Callback aufgerufen; `--strict-preflight` wirft;
  Default wirft nicht; resume ruft Pre-Flight nicht.
- **qualrun:** `max_tokens` inkl. Headroom; `extra_body` durchgereicht; `reasoning_text` nur bei
  `content_empty` persistiert.
- **judge:** reasoning-only → `unscored=True`, kein `score=1`, richtige Rationale; echt-leer →
  `score=1`; not ok → unverändert.
- **scorecard:** reasoning-only-Verdicts aus dem Mittel ausgeschlossen; unscored-Quote im Render.
- **judge-picker:** `discover_models` generisch; `discover_judge_endpoint_models` lädt JudgeConfig;
  `/judge-endpoint-models` never-500 + Path-Guard; `--judge-model` override; `start_judge` argv.
- **GUI-Smoke:** headless Chrome — Judge-Dropdown füllt sich bei Config-Wechsel; Pre-Flight-Event
  rendert in der Live-Karte. (GUI nach Änderung neu starten; JS headless prüfen.)

## 11. Doku-Updates
- `AGENTS.md` Gotchas: reasoning-only → unscored (nicht mehr 1/5); Pre-Flight + `--strict-preflight`;
  `reasoning_headroom_tokens`/`extra_body`; Judge-Picker.
- `docs/explanation/design-decisions.md`: warum unscored statt 1/5, warum Budget opt-in.
- `templates/_method_explainer.html`: die reasoning-only/unscored-Behandlung im UI erklären
  (Bewertungs-Methode bleibt transparent + UI-abrufbar).

## 12. Reihenfolge der Umsetzung
1. Schema-Additive (`ModelSpec`, `EvalResponse`, Protocol/Signaturen) + Durchreichung
   `extra_body`/Headroom (Komponente ② 5a/5c) — Fundament, viele andere Tests hängen daran.
2. Judge-Bewertung (② 5b/5d) — kleinster, klar abgegrenzter Verhaltens-Change.
3. Pre-Flight (①) — baut auf dem effektiven Budget aus 1 auf.
4. Judge-Picker (③) — orthogonal, GUI-lastig, zuletzt (separater headless-Smoke).
5. Doku (§11) parallel zu jeder Komponente.
