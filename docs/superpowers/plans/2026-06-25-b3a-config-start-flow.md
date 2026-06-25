# B3a — Konfig+Start Flow-Stationen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/config` (`config.html`) von drei parallelen Cards zu einem Flow-Stationen-Layout umbauen — Eval → Judge → Ergebnis als sichtbare, je eigenständige Sequenz; Import/Resume als kompakte Nebeneinstiege; kontextabhängige Default-Station. Reine Template-/Anordnungs-Arbeit, kein Funktions-Umbau.

**Architecture:** Ein Alpine-`x-data` über der Seite hält die aktive Station; eine Flow-Leiste (Eval → Judge → Ergebnis, klickbar, ohne 1/2/3-Zahlen) klappt die aktive Station auf. Die Eval- und Judge-Blöcke wandern unverändert in Partials. Die Default-Station wird client-seitig aus den bereits gelieferten Kontext-Daten (`eval_only_bundles`, `resume`) bestimmt — **keine** Änderung an der `config_get`-Route.

**Tech Stack:** FastAPI + Jinja2 (`[gui]` extra) · Alpine.js · `model_picker.js` (unverändert) · pytest · ruff · mypy strict.

## Global Constraints

- Python 3.12, `uv` only. Ruff (line-length 100) + mypy strict nach **jedem** Task grün (`uv run ruff check . && uv run ruff format . && uv run mypy touchstone/`).
- **Scope-Disziplin:** nur die je Task genannten Dateien. Format nur geänderte Python-Dateien (`uv run ruff format <files>`), **nie** `ruff format .` tree-weit. `git add` explizit, nie `git add -A`. `git status --short` vor jedem Commit.
- **Git-Disziplin (Subagenten):** Commit NUR auf dem aktuellen Branch — **kein** `git checkout`/`merge`/`push`/main-touch.
- **Kein Funktions-Umbau:** Modell-Picker (`x-data="modelPicker(...)"`, Checkboxen, Ad-hoc, „vom Endpoint hinzufügen", `models_json`-Hidden-Field), Judge-Modell-Picker (`judgeModelPicker()`), Conflict/Error-Banner bleiben **funktional identisch** — nur umgeordnet.
- **Routen unberührt:** `/runs/eval`, `/runs/judge`, `/import-bundle`, `/endpoint-models`, `/judge-endpoint-models`, `/config-view/…`, `config_get` selbst — **nicht ändern**.
- **`model_picker.js` bleibt nicht-deferred** und VOR Alpine eingebunden (es registriert `modelPicker` vor `alpine:init`) — die Script-Reihenfolge in `config.html` beibehalten (Kommentar dokumentiert es).
- **Keine 1/2/3-Zahlen** im In-Seite-Flow (kollidieren mit Sidebar 1/3/6) — Marker = Aktions-Namen + Pfeile. Deutsch in UI-Text.

---

### Task 1: `config.html` → Flow-Stationen (Eval/Judge/Ergebnis) + kontextabhängige Default-Station

**Files:**
- Modify: `touchstone/gui/templates/config.html`
- Create: `touchstone/gui/templates/macros/_config_eval.html`, `touchstone/gui/templates/macros/_config_judge.html`
- Test: `tests/test_gui_config_flow.py` (neu)

**Interfaces:**
- Consumes (Route-Kontext, unverändert): `packs`, `configs`, `models_by_config`, `judge_configs`, `eval_only_bundles` (list[str]), `resume` (str|None), `bundle` (str|None), `conflict` (bool), `error`.
- Produces: `config.html` rendert eine Flow-Leiste + Accordion mit den Stationen „Eval starten" / „Judge starten" + einem Ergebnis-Verweis auf `/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_config_flow.py
from fastapi.testclient import TestClient
from touchstone.gui import app as appmod
from touchstone.gui.control import RunRegistry


class _FakeLauncher:
    def spawn(self, argv): return 1
    def alive(self, pid): return False
    def terminate(self, pid): return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(appmod.create_app(runs_dir=tmp_path, registry=reg))


def test_config_renders_flow_stations(tmp_path):
    body = _client(tmp_path).get("/config").text
    # the flow markers (Eval → Judge → Ergebnis), no 1/2/3 station numbers
    assert "Eval starten" in body
    assert "Judge starten" in body
    assert "Ergebnis" in body
    # an Alpine scope holding the active station
    assert "x-data" in body and "station" in body
    # result step links to the overview
    assert 'href="/"' in body


def test_config_preserves_eval_and_judge_function_markers(tmp_path):
    body = _client(tmp_path).get("/config").text
    # model picker + judge picker + their hidden/submit machinery survive the reorder
    assert "modelPicker(" in body
    assert "judgeModelPicker(" in body
    assert 'name="models_json"' in body
    assert 'action="/runs/eval"' in body and 'action="/runs/judge"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_config_flow.py -v`
Expected: FAIL — `config.html` has no flow stations / `x-data station` scope yet.

- [ ] **Step 3: Implement**

1. Lies `config.html` vollständig. Extrahiere den **Eval-Block** (die `<form action="/runs/eval">`-Card, inkl. Resume-Zweig + `modelPicker`-`x-data`) nach `macros/_config_eval.html` und den **Judge-Block** (`<form action="/runs/judge">`-Card mit `judgeModelPicker()`) nach `macros/_config_judge.html` — **unverändert im Inhalt** (nur ausgeschnitten + via `{% include %}` eingebunden).
2. In `config.html`: behalte die `<script src="/static/model_picker.js">`-Zeile **nicht-deferred, ganz oben** (mit dem bestehenden Kommentar). Lege einen Seiten-`x-data` an, der die aktive Station hält und die Default-Station kontextabhängig setzt:
   ```jinja
   <div x-data="{ station: '{{ 'judge' if (eval_only_bundles and not resume) else 'eval' }}' }">
   ```
3. **Flow-Leiste** (klickbare Marker, keine Zahlen):
   ```html
   <div class="flow-bar">
     <button @click="station='eval'" :class="station==='eval' && 'active'">Eval starten</button>
     <span>→</span>
     <button @click="station='judge'" :class="station==='judge' && 'active'">Judge starten</button>
     <span>→</span>
     <a href="/">Ergebnis ansehen</a>
   </div>
   ```
4. **Stationen** (Accordion via `x-show`): `<div x-show="station==='eval'">{% include "macros/_config_eval.html" %}</div>` und analog für `judge`. Die Conflict/Error-Banner bleiben oben (vor der Flow-Leiste).
5. **Judge-Hinweis:** im Judge-Block (oder davor) bei nicht-leerem `eval_only_bundles` einen dezenten Hinweis: `{% if eval_only_bundles %}<p class="muted text-xs">{{ eval_only_bundles|length }} Bundle(s) bereit zum Bewerten.</p>{% endif %}`.
6. **Ergebnis-Verweis:** der „Ergebnis ansehen"-Link in der Flow-Leiste zeigt auf `/` (Übersicht).

(Falls `app.css` einen `.flow-bar`-Stil braucht: füge minimale Regeln dort hinzu — das ist erlaubt, `static/app.css` zur Scope-Liste nehmen und im Commit nennen. Inline-Styles wie im Rest der Templates sind auch ok, dann css unberührt.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_config_flow.py -v && uv run pytest -q && uv run mypy touchstone/`
Expected: PASS, volle Suite grün (bestehende `/config`-bezogene Tests bleiben grün).

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/config.html touchstone/gui/templates/macros/_config_eval.html touchstone/gui/templates/macros/_config_judge.html tests/test_gui_config_flow.py
# falls app.css geändert: zusätzlich git add touchstone/gui/static/app.css
git commit -m "feat(gui): /config as flow stations (Eval→Judge→Ergebnis) + context default station"
```

---

### Task 2: Import + Resume als kompakte Nebeneinstiege

**Files:**
- Modify: `touchstone/gui/templates/config.html`
- Create: `touchstone/gui/templates/macros/_config_sidesteps.html`
- Test: `tests/test_gui_config_flow.py`

**Interfaces:**
- Consumes: `resume` (str|None) aus dem Route-Kontext. Das Import-Formular postet an das bestehende `/import-bundle`.

- [ ] **Step 1: Write the failing test**

```python
def test_config_sidesteps_import_always_resume_conditional(tmp_path):
    # no resume → import present, resume hint absent
    body = _client(tmp_path).get("/config").text
    assert "Bundle importieren" in body
    assert 'action="/import-bundle"' in body
    assert "Fortsetzen" not in body          # resume hint only in resume mode
    # resume mode → resume hint present
    body2 = _client(tmp_path).get("/config?resume=2026-01-01_run").text
    assert "Fortsetzen" in body2
    assert "2026-01-01_run" in body2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_config_flow.py::test_config_sidesteps_import_always_resume_conditional -v`
Expected: FAIL — sidesteps not yet structured (or "Fortsetzen" leaks outside resume mode).

- [ ] **Step 3: Implement**

1. Extrahiere den **Bundle-Import-Block** (die `<form action="/import-bundle">`-Card) nach `macros/_config_sidesteps.html` und binde ihn unter den Stationen als kompakten, klar abgesetzten Bereich ein (z. B. unter einer dezenten Überschrift „Weitere Aktionen" / `text-sm muted`).
2. **Resume** als Nebeneinstieg: der Resume-Modus (`{% if resume %}` mit „Fortsetzen: …") wird im Eval-Block angezeigt (Resume ist eine Eval-Fortsetzung) — stelle sicher, dass „Fortsetzen"/der Resume-Hinweis **nur** bei gesetztem `resume` rendert (kein Leak in den Normal-Modus). Da `_config_eval.html` (Task 1) den Resume-Zweig schon trägt, verifiziert dieser Task primär die Bedingung; ergänze in `_config_sidesteps.html` nur den Import. Falls der Resume-Hinweis im Normalmodus leakt, hier fixen.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_config_flow.py -v && uv run pytest -q && uv run mypy touchstone/ && uv run ruff check .`
Expected: PASS, volle Suite grün, lint + types clean.

- [ ] **Step 5: Commit**

```bash
git add touchstone/gui/templates/config.html touchstone/gui/templates/macros/_config_sidesteps.html tests/test_gui_config_flow.py
git commit -m "feat(gui): import/resume as compact side-entries on the config flow page"
```

---

## Notes für die Umsetzung

- **Kein Funktions-Umbau:** Eval-/Judge-Block-Inhalt 1:1 ausschneiden + einbinden; die Tests `modelPicker(`/`judgeModelPicker(`/`models_json`/`action="/runs/…"` sind die Regression-Guards.
- **`model_picker.js`-Script-Reihenfolge** (nicht-deferred, vor Alpine) beim Umbau beibehalten.
- **`config_get`-Route nicht anfassen** — die Default-Station kommt rein aus `eval_only_bundles`/`resume` im Template.
- Nach jedem Task: `uv run pytest -q && uv run ruff check . && uv run mypy touchstone/` grün.
