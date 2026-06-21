# Modell-Auswahl in der GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auf `/config` auswählen können, welche Modelle ein Eval-Lauf fährt — Checkboxen für die `models:` der gewählten Config + Ad-hoc-Eingabe (id/quant), ephemer (ersetzt `config.models` nur für diesen Lauf).

**Architecture:** Pure Parse-Helfer (`models_from_json`/`apply_models_override` in `config.py`; `config_models`/`models_by_config` in neuem `ramcheck/gui/configs.py`) tragen die Logik. Die `/config`-Route bettet die Modelle aller Configs als JSON ein; ein Alpine-Picker (`config.html` + `static/model_picker.js`) baut daraus ein verstecktes `models_json`-Feld. `/runs/eval` validiert es und reicht es als `models` an `RunRegistry.start_eval` durch, das `--models-json` in die `eval`-argv schreibt; `eval_cmd` ersetzt damit `config.models` vor dem Lauf. Kein Zurückschreiben in Configs.

**Tech Stack:** Python 3.12 · pydantic v2 (`Config`/`ModelSpec`) · Typer-CLI · FastAPI · Jinja2 · Alpine (build-frei) · pytest (`uv run pytest`).

---

## Pre-flight (für den ausführenden Worker)

- **Working dir:** `/Users/Shared/code/llm-benchmark-harness`; Tests via `uv run pytest` aus dem Repo-Root (cwd hat die echten `config*.yaml`, die die `/config`-Route globt).
- **Gates nach jeder Task:** die jeweilige Testdatei; am Ende (Task 7): `uv run pytest -q`, `uv run mypy ramcheck`, `uv run ruff check ramcheck tests`, `uv run ruff format --check ramcheck tests`.
- **Commit-Trailer:** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. **Branch:** `feat/modell-auswahl-gui` ist ausgecheckt. Kein Push (macht der Controller am Ende).

## Verifizierte Fakten (aus der Codebase)

- `config.py`: `class ModelSpec(BaseModel): id: str; quant: str = ""; max_tokens_default: int = 400`. `class Config(BaseModel)` mit `models: list[ModelSpec]` + Validator `_at_least_one_model` (≥1). `load_config(path) -> Config`. pydantic v2 (`model_dump()`, `model_copy(update=…)`). `config.py` importiert bereits `yaml`, und aus pydantic `BaseModel, Field, field_validator`.
- `ramcheck/gui/control.py`: `RunRegistry.start_eval(self, *, pack_path: str, config_path: str, resume_dir: Path | None = None) -> RunHandle` baut `argv = ["eval", "--pack", pack_path, "--config", config_path, "--run-dir", str(run_dir), "--emit-events"]` (+ `--resume`), spawnt via `self.launcher.spawn(argv)`. Sentinel wird vor Spawn geschrieben. `import json` ist vorhanden (Sentinels sind JSON).
- `ramcheck/cli.py`: `eval_cmd(...)` mit Optionen `--pack`, `--config/-c`, `--out`, `--resume`, `--run-dir`, `--web`, `--port`, `--no-open`, `--emit-events`. Lädt `cfg = load_config(config)` (Zeile ~511). `console` (rich) ist im Modul verfügbar.
- `ramcheck/gui/app.py` `/runs/eval` (POST): `start_eval(pack_path=Form(...), config_path=Form(...), resume_dir=Form(None))`; confined via `_confine_cwd`/`_confine`; ruft `registry.start_eval(...)`; gibt `{"run_dir":…,"kind":…}` (JSON) zurück; `RunInProgress` → `HTTPException(409)`. `/config` (GET) liefert `packs`, `configs` (`Path('.').glob('config*.yaml')`), `judge_configs`, `eval_only_bundles`, `resume`, `bundle`, `conflict`, `error`, `active`.
- `config.html`: `<form method="post" action="/runs/eval">` mit `<select name="pack_path">` + `<select name="config_path">` + Submit. **Plain POST** (kein hx-*) → zeigt die JSON-Antwort. base.html lädt `alpine.min.js` (defer) + `htmx.min.js`.
- Test-Muster: `tests/test_gui_control_registry.py` hat `FakeLauncher` (records `calls`), `control.RunRegistry(runs_dir=tmp_path, launcher=…)`. GUI-Routen: `gui_app.create_app(runs_dir=tmp_path, registry=RunRegistry(...))` + `TestClient`. `uv run pytest`.

## File Structure

| Datei | Verantwortung |
|---|---|
| `ramcheck/config.py` *(ändern)* | `models_from_json(s) -> list[ModelSpec]` (parse+validate, ≥1) + `apply_models_override(cfg, s) -> Config` (replace, leer → unverändert) — pur |
| `ramcheck/gui/configs.py` *(neu, pur)* | `config_models(path) -> list[ModelSpec]` (defensiv) + `models_by_config(files) -> dict[str, list[dict]]` |
| `ramcheck/gui/control.py` *(ändern)* | `start_eval(..., models=None)` → `--models-json` in argv |
| `ramcheck/cli.py` *(ändern)* | `eval_cmd --models-json` → `apply_models_override(cfg, …)` |
| `ramcheck/gui/app.py` *(ändern)* | `/config` reicht `models_by_config`; `/runs/eval` nimmt + validiert `models_json`, ruft `start_eval(models=…)` |
| `ramcheck/gui/templates/config.html` *(ändern)* | Alpine-Picker (Checkboxen + Ad-hoc + hidden `models_json`), bei `resume` aus |
| `ramcheck/gui/static/model_picker.js` *(neu)* | Alpine-Komponente `modelPicker(byConfig)` |
| `tests/test_config_models_override.py` *(neu)* | Unit: `models_from_json` + `apply_models_override` |
| `tests/test_gui_configs.py` *(neu)* | Unit: `config_models` + `models_by_config` |
| `tests/test_gui_eval_models.py` *(neu)* | start_eval-argv + Route-Verhalten + `/config`-Render |
| `AGENTS.md` *(ändern)* | Notiz zum GUI-Modell-Override |

---

## Task 1: `config.py` — `models_from_json` + `apply_models_override` (pure)

**Files:**
- Modify: `ramcheck/config.py`
- Test: `tests/test_config_models_override.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_models_override.py
from __future__ import annotations

import pytest

from ramcheck.config import ModelSpec, apply_models_override, load_config, models_from_json


def test_models_from_json_valid():
    specs = models_from_json('[{"id":"a","quant":"Q4"},{"id":"b"}]')
    assert specs == [ModelSpec(id="a", quant="Q4"), ModelSpec(id="b")]
    assert specs[1].max_tokens_default == 400  # default applied


def test_models_from_json_rejects_empty_array():
    with pytest.raises(ValueError):
        models_from_json("[]")


def test_models_from_json_rejects_bad_json():
    with pytest.raises(ValueError):
        models_from_json("{not json")


def test_models_from_json_rejects_missing_id():
    with pytest.raises(ValueError):
        models_from_json('[{"quant":"Q4"}]')


def test_models_from_json_rejects_non_array():
    with pytest.raises(ValueError):
        models_from_json('{"id":"a"}')


def test_apply_models_override_empty_returns_same_config():
    cfg = load_config("config.m5.yaml")
    assert apply_models_override(cfg, "") is cfg
    assert apply_models_override(cfg, "   ") is cfg


def test_apply_models_override_replaces_models():
    cfg = load_config("config.m5.yaml")
    out = apply_models_override(cfg, '[{"id":"x","quant":"Q2"}]')
    assert [m.id for m in out.models] == ["x"]
    assert out.endpoint == cfg.endpoint  # everything else preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_models_override.py -q`
Expected: FAIL — `ImportError: cannot import name 'models_from_json'`.

- [ ] **Step 3: Write minimal implementation**

At the top of `ramcheck/config.py`, ensure these imports exist (add what's missing):

```python
import json

from pydantic import BaseModel, Field, ValidationError, field_validator
```

Add at the end of `ramcheck/config.py` (after `load_config`):

```python
def models_from_json(s: str) -> list[ModelSpec]:
    """Parse a JSON array of model specs (GUI picker). Raises ValueError on bad JSON,
    a non-array, an empty array, or a spec missing required fields."""
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid models JSON: {e}") from e
    if not isinstance(data, list) or not data:
        raise ValueError("models_json must be a non-empty JSON array")
    try:
        return [ModelSpec(**m) for m in data]
    except (ValidationError, TypeError) as e:
        raise ValueError(f"invalid model spec: {e}") from e


def apply_models_override(cfg: Config, models_json: str) -> Config:
    """Return cfg with its models replaced by ``models_json`` (the GUI picker selection).
    An empty/blank string means 'no override' and returns cfg unchanged (resume / CLI default)."""
    if not models_json.strip():
        return cfg
    return cfg.model_copy(update={"models": models_from_json(models_json)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_models_override.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/config.py tests/test_config_models_override.py
git commit -m "feat(config): models_from_json + apply_models_override (GUI model override)"
```

---

## Task 2: `ramcheck/gui/configs.py` — `config_models` + `models_by_config` (pure)

**Files:**
- Create: `ramcheck/gui/configs.py`
- Test: `tests/test_gui_configs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_configs.py
from __future__ import annotations

from ramcheck.gui import configs


def _write(p, text):
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_config_models_parses_models(tmp_path):
    c = _write(
        tmp_path / "config.x.yaml",
        "endpoint: {base_url: 'http://x/v1'}\nmachine: m\n"
        "models:\n  - {id: 'a', quant: 'Q4'}\n  - {id: 'b'}\n",
    )
    specs = configs.config_models(c)
    assert [m.id for m in specs] == ["a", "b"]
    assert specs[0].quant == "Q4"
    assert specs[1].max_tokens_default == 400


def test_config_models_defensive_on_missing_file(tmp_path):
    assert configs.config_models(tmp_path / "nope.yaml") == []


def test_config_models_defensive_on_broken_yaml(tmp_path):
    c = _write(tmp_path / "bad.yaml", "models: [unterminated\n")
    assert configs.config_models(c) == []


def test_config_models_defensive_on_no_models_key(tmp_path):
    c = _write(tmp_path / "nomodels.yaml", "machine: m\n")
    assert configs.config_models(c) == []


def test_models_by_config_maps_and_survives_one_broken(tmp_path):
    good = _write(tmp_path / "config.good.yaml", "models:\n  - {id: 'g'}\n")
    bad = _write(tmp_path / "config.bad.yaml", "models: [oops\n")
    out = configs.models_by_config([good, bad])
    assert out[good] == [{"id": "g", "quant": "", "max_tokens_default": 400}]
    assert out[bad] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_configs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ramcheck.gui.configs'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ramcheck/gui/configs.py
"""Pure helpers to surface the models defined inside config*.yaml files for the
Konfig+Start model picker. Defensive: a broken/missing config yields no models so a
single bad file never breaks the page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ramcheck.config import ModelSpec


def config_models(path: str | Path) -> list[ModelSpec]:
    """The validated ``models:`` of a config file, or [] on any read/parse error.

    Parses only the models list (not the full Config) so a config with a placeholder
    endpoint still shows its models in the picker.
    """
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    items = raw.get("models")
    if not isinstance(items, list):
        return []
    out: list[ModelSpec] = []
    for m in items:
        if not isinstance(m, dict):
            continue
        try:
            out.append(ModelSpec(**m))
        except Exception:
            continue
    return out


def models_by_config(files: list[str]) -> dict[str, list[dict[str, Any]]]:
    """{config_path: [model.model_dump(), ...]} for embedding into the template (JSON)."""
    return {f: [m.model_dump() for m in config_models(f)] for f in files}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_configs.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/configs.py tests/test_gui_configs.py
git commit -m "feat(gui): config_models + models_by_config (pure picker source)"
```

---

## Task 3: `RunRegistry.start_eval(models=...)` → `--models-json` argv

**Files:**
- Modify: `ramcheck/gui/control.py`
- Test: `tests/test_gui_eval_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_eval_models.py
from __future__ import annotations

import json

from ramcheck.config import ModelSpec
from ramcheck.gui import control


class _Rec:
    def __init__(self):
        self.calls = []

    def spawn(self, argv):
        self.calls.append(argv)
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def test_start_eval_adds_models_json_when_given(tmp_path):
    rec = _Rec()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=rec)
    reg.start_eval(
        pack_path="packs/ndassist.yaml",
        config_path="config.m5.yaml",
        models=[ModelSpec(id="a", quant="Q4"), ModelSpec(id="b")],
    )
    argv = rec.calls[0]
    assert "--models-json" in argv
    payload = json.loads(argv[argv.index("--models-json") + 1])
    assert [m["id"] for m in payload] == ["a", "b"]
    assert payload[0]["quant"] == "Q4"


def test_start_eval_omits_models_json_when_none(tmp_path):
    rec = _Rec()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=rec)
    reg.start_eval(pack_path="p", config_path="c")
    assert "--models-json" not in rec.calls[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_eval_models.py -q`
Expected: FAIL — `TypeError: start_eval() got an unexpected keyword argument 'models'`.

- [ ] **Step 3: Write minimal implementation**

In `ramcheck/gui/control.py`, add the import near the other imports at the top:

```python
from ramcheck.config import ModelSpec
```

Change `start_eval` to accept `models` and add the flag (only the signature + the argv block change):

```python
    def start_eval(
        self,
        *,
        pack_path: str,
        config_path: str,
        resume_dir: Path | None = None,
        models: list[ModelSpec] | None = None,
    ) -> RunHandle:
        # Hold the lock across guard+reserve+sentinel+spawn so no window opens between
        # the guard and the on-disk lock being written (TOCTOU).
        with self._lock:
            self._guard_free()
            run_dir = resume_dir or self._new_run_dir(pack_path)
            argv = [
                "eval",
                "--pack",
                pack_path,
                "--config",
                config_path,
                "--run-dir",
                str(run_dir),
                "--emit-events",
            ]
            if resume_dir is not None:
                argv += ["--resume", str(resume_dir)]
            if models:
                argv += ["--models-json", json.dumps([m.model_dump() for m in models])]
            write_sentinel(
                run_dir, kind="eval", pid=-1, pack_path=pack_path, config_path=config_path
            )
            pid = self.launcher.spawn(argv)
            set_sentinel_pid(run_dir, pid)
            return RunHandle("eval", run_dir, pid)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_eval_models.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/control.py tests/test_gui_eval_models.py
git commit -m "feat(gui): start_eval passes selected models as --models-json"
```

---

## Task 4: `eval_cmd --models-json` wiring

**Files:**
- Modify: `ramcheck/cli.py`
- Test: `tests/test_gui_eval_models.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gui_eval_models.py
from typer.testing import CliRunner

from ramcheck.cli import app as cli_app


def test_eval_cmd_exposes_models_json_option():
    res = CliRunner().invoke(cli_app, ["eval", "--help"])
    assert res.exit_code == 0
    assert "--models-json" in res.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_eval_models.py::test_eval_cmd_exposes_models_json_option -q`
Expected: FAIL — `--models-json` not in help output.

- [ ] **Step 3: Write minimal implementation**

In `ramcheck/cli.py`, add the import (with the other `ramcheck.config` imports):

```python
from ramcheck.config import apply_models_override
```

Add a new option to `eval_cmd` (after the `emit_events` option, before the closing `) -> None:`):

```python
    models_json: str = typer.Option(
        "", "--models-json", help="JSON list[ModelSpec]; replaces config.models for this run (GUI picker)"
    ),
```

Apply it right after `cfg = load_config(config)`:

```python
    cfg = load_config(config)
    try:
        cfg = apply_models_override(cfg, models_json)
    except ValueError as e:
        console.print(f"[red]--models-json:[/] {e}")
        raise typer.Exit(1) from None
    pk = load_pack(pack)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_eval_models.py -q`
Expected: PASS (3 passed in file).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/cli.py tests/test_gui_eval_models.py
git commit -m "feat(cli): eval --models-json replaces config.models for the run"
```

---

## Task 5: `/runs/eval` route accepts + validates `models_json`

**Files:**
- Modify: `ramcheck/gui/app.py`
- Test: `tests/test_gui_eval_models.py` (append)

> **Note (intentional, codebase-consistent deviation from spec §6):** the existing `/runs/eval` returns JSON on success and raises `HTTPException` on error (409 for RunInProgress). For consistency, invalid/empty `models_json` raises **`HTTPException(400)`** (not an HTML re-render). The behavioral guarantee — *no spawn on invalid input* — is preserved.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gui_eval_models.py
from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app


def _client_and_launcher(tmp_path):
    rec = _Rec()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=rec)
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg)), rec


def test_route_valid_models_json_spawns_with_flag(tmp_path):
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={
            "pack_path": "packs/ndassist.yaml",
            "config_path": "config.m5.yaml",
            "models_json": '[{"id":"a","quant":"Q4"}]',
        },
    )
    assert r.status_code == 200
    assert "--models-json" in rec.calls[0]


def test_route_empty_array_is_400_and_no_spawn(tmp_path):
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={"pack_path": "p", "config_path": "c", "models_json": "[]"},
    )
    assert r.status_code == 400
    assert rec.calls == []


def test_route_invalid_models_json_is_400_and_no_spawn(tmp_path):
    client, rec = _client_and_launcher(tmp_path)
    r = client.post(
        "/runs/eval",
        data={"pack_path": "p", "config_path": "c", "models_json": "{bad"},
    )
    assert r.status_code == 400
    assert rec.calls == []


def test_route_no_models_json_still_works(tmp_path):
    client, rec = _client_and_launcher(tmp_path)
    r = client.post("/runs/eval", data={"pack_path": "p", "config_path": "c"})
    assert r.status_code == 200
    assert "--models-json" not in rec.calls[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_eval_models.py -k route -q`
Expected: FAIL — valid case has no `--models-json`; "[]"/invalid return 200 (not 400) because the field is ignored.

- [ ] **Step 3: Write minimal implementation**

In `ramcheck/gui/app.py`, add the import at the top (with the other `ramcheck.config`/`ramcheck.gui` imports):

```python
from ramcheck.config import models_from_json
```

Change the `/runs/eval` route to accept and validate `models_json`:

```python
    @app.post("/runs/eval")
    def start_eval(
        pack_path: str = Form(...),
        config_path: str = Form(...),
        resume_dir: str | None = Form(None),
        models_json: str = Form(""),
    ) -> Any:
        pack_path = _confine_cwd(pack_path)
        config_path = _confine_cwd(config_path)
        resume: Path | None = _confine(resume_dir) if resume_dir else None
        # Resume never overrides models (the bundle's cells are fixed); otherwise apply
        # the picker selection. Invalid/empty selection must not spawn a run.
        models = None
        if resume is None and models_json.strip():
            try:
                models = models_from_json(models_json)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from None
        try:
            h = registry.start_eval(
                pack_path=pack_path, config_path=config_path, resume_dir=resume, models=models
            )
        except RunInProgress as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"run_dir": h.run_dir.name, "kind": h.kind}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_eval_models.py -q`
Expected: PASS (all in file).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/app.py tests/test_gui_eval_models.py
git commit -m "feat(gui): /runs/eval validates models_json and forwards it (400, no spawn, on invalid)"
```

---

## Task 6: `config.html` picker + `model_picker.js` + `/config` data

**Files:**
- Modify: `ramcheck/gui/app.py` (`/config` route passes `models_by_config`)
- Create: `ramcheck/gui/static/model_picker.js`
- Modify: `ramcheck/gui/templates/config.html`
- Test: `tests/test_gui_eval_models.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gui_eval_models.py
def test_config_page_renders_model_picker(tmp_path):
    client, _ = _client_and_launcher(tmp_path)
    body = client.get("/config").text
    assert "modelPicker(" in body            # Alpine component bound
    assert 'name="models_json"' in body      # hidden field present
    assert "/static/model_picker.js" in body
    assert "+ Modell" in body                # ad-hoc add button


def test_config_page_hides_picker_on_resume(tmp_path):
    client, _ = _client_and_launcher(tmp_path)
    body = client.get("/config?resume=somebundle").text
    assert "modelPicker(" not in body
    assert 'name="models_json"' not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_eval_models.py -k config_page -q`
Expected: FAIL — no `modelPicker(` / `models_json` in the rendered config page.

- [ ] **Step 3a: `/config` route passes `models_by_config`**

In `ramcheck/gui/app.py`, add the import (with the other `ramcheck.gui` imports):

```python
from ramcheck.gui import configs as configs_mod
```

In the `config_get` route, after `config_files = ...`, add and pass it:

```python
        return render(
            "config.html",
            request,
            packs=pack_files,
            configs=config_files,
            models_by_config=configs_mod.models_by_config(config_files),
            judge_configs=judge_config_files,
            eval_only_bundles=eval_only,
            resume=resume,
            bundle=bundle,
            conflict=conflict,
            error=None,
            active="config",
        )
```

- [ ] **Step 3b: Create `ramcheck/gui/static/model_picker.js`**

```javascript
// ramcheck/gui/static/model_picker.js
// Alpine component for the Konfig+Start model picker. Given {config_path: [model,...]},
// it shows the selected config's models as checkboxes (+ ad-hoc id/quant rows) and keeps
// a hidden models_json field in sync. Build-free; registered on alpine:init.
"use strict";

document.addEventListener("alpine:init", () => {
  Alpine.data("modelPicker", (byConfig) => ({
    byConfig: byConfig,
    config: Object.keys(byConfig)[0] || "",
    models: [],
    adhoc: [],
    init() {
      this.syncFromConfig();
    },
    syncFromConfig() {
      const list = this.byConfig[this.config] || [];
      // copy + default-checked; never mutate byConfig
      this.models = list.map((m) => ({
        id: m.id,
        quant: m.quant || "",
        max_tokens_default: m.max_tokens_default || 400,
        on: true,
      }));
      this.adhoc = [];
    },
    addAdhoc() {
      this.adhoc.push({ id: "", quant: "" });
    },
    removeAdhoc(i) {
      this.adhoc.splice(i, 1);
    },
    count() {
      const checked = this.models.filter((m) => m.on).length;
      const added = this.adhoc.filter((a) => a.id.trim()).length;
      return checked + added;
    },
    modelsJson() {
      const out = [];
      for (const m of this.models) {
        if (m.on) {
          out.push({ id: m.id, quant: m.quant, max_tokens_default: m.max_tokens_default });
        }
      }
      for (const a of this.adhoc) {
        if (a.id.trim()) {
          out.push({ id: a.id.trim(), quant: a.quant.trim(), max_tokens_default: 400 });
        }
      }
      return JSON.stringify(out);
    },
  }));
});
```

- [ ] **Step 3c: `config.html` — picker inside the eval form**

Add the script include after `{% block body %}` (near the top of the body block):

```html
<script defer src="/static/model_picker.js"></script>
```

Replace the eval form's Config form-group + add the picker. The current Config block is:

```html
    <div class="form-group">
      <label class="form-label" for="config_path">Config</label>
      <select class="form-select" id="config_path" name="config_path">
        {% for c in configs %}
        <option value="{{ c }}">{{ c }}</option>
        {% endfor %}
      </select>
    </div>
```

Replace it with (wraps the config select + picker in one Alpine scope):

```html
    <div x-data='modelPicker({{ models_by_config | tojson }})'>
      <div class="form-group">
        <label class="form-label" for="config_path">Config</label>
        <select class="form-select" id="config_path" name="config_path"
                x-model="config" @change="syncFromConfig()">
          {% for c in configs %}
          <option value="{{ c }}">{{ c }}</option>
          {% endfor %}
        </select>
      </div>
      {% if not resume %}
      <div class="form-group">
        <label class="form-label">Modelle</label>
        <template x-for="(m, i) in models" :key="i">
          <label style="display:block; font-size:0.9rem">
            <input type="checkbox" x-model="m.on">
            <span x-text="m.id"></span>
            <span class="muted text-xs" x-text="m.quant ? ('· ' + m.quant) : ''"></span>
          </label>
        </template>
        <template x-for="(a, i) in adhoc" :key="'a' + i">
          <div class="flex gap-2" style="margin-top:0.25rem">
            <input class="form-input" type="text" placeholder="id" x-model="a.id" style="flex:1">
            <input class="form-input" type="text" placeholder="quant" x-model="a.quant" style="width:8rem">
            <button type="button" class="btn btn-secondary" @click="removeAdhoc(i)">✕</button>
          </div>
        </template>
        <button type="button" class="btn btn-secondary" style="margin-top:0.4rem" @click="addAdhoc()">+ Modell</button>
        <input type="hidden" name="models_json" :value="modelsJson()">
        <p class="muted text-xs" x-show="count() === 0" style="color:var(--nein); margin-top:0.25rem">
          Wähle mindestens ein Modell.
        </p>
      </div>
      {% endif %}
    </div>
```

> The existing submit `<button type="submit" class="btn btn-primary">` stays. (Optional polish: add `:disabled="count() === 0"` if it falls inside the `x-data` scope; only do so if the button is moved inside the `<div x-data=…>`. Keep it simple — the server already 400s an empty selection.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_eval_models.py -q`
Expected: PASS (all in file).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/app.py ramcheck/gui/static/model_picker.js ramcheck/gui/templates/config.html tests/test_gui_eval_models.py
git commit -m "feat(gui): model picker on Konfig+Start (checkboxes + ad-hoc, hidden models_json)"
```

---

## Task 7: AGENTS.md + finale Gates

**Files:**
- Modify: `AGENTS.md`
- Test: full suite + static gates

- [ ] **Step 1: Update AGENTS.md**

Add to the GUI/control section:

```markdown
- **GUI-Modell-Override (ephemer):** Die „Konfig + Start"-Seite zeigt die `models:` der gewählten
  Config als Checkboxen + eine Ad-hoc-Zeile (id/quant). Die Auswahl geht als `models_json` an
  `/runs/eval` → `RunRegistry.start_eval(models=…)` → `eval --models-json` → `apply_models_override`
  **ersetzt** `config.models` nur für diesen Lauf (Endpoint/seed bleiben aus der Config; nichts wird
  in die Config zurückgeschrieben — das Bundle protokolliert, was lief). Leeres/ungültiges
  `models_json` → 400, kein Spawn; `resume` ignoriert den Override. Pure Logik:
  `config.models_from_json`/`apply_models_override`, `gui/configs.py`.
```

- [ ] **Step 2: Full test suite**

Run: `uv run pytest -q`
Expected: PASS — alle bisherigen + neuen Tests, 0 Fehler.

- [ ] **Step 3: Static gates**

Run:
```bash
uv run mypy ramcheck
uv run ruff check ramcheck tests
uv run ruff format --check ramcheck tests
```
Expected: clean. Bei Format-Funden: `uv run ruff format ramcheck tests` und neu stagen.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): GUI model override (--models-json, ephemeral, replaces config.models)"
```

- [ ] **Step 5: Manual smoke (für den Controller / Johannes)**

GUI starten → `/config` → eine Config wählen → ihre Modelle erscheinen (alle an) → eines abwählen, ein zweites ad-hoc eintippen → „Eval starten" → der Lauf fährt genau diese Modelle (gegen den Endpoint der Config) → danach `/compare/<bundle>?axis=model` zeigt sie nebeneinander.

---

## Self-Review (gegen die Spec)

**Spec coverage:**
- M1 (Picker: Config-Checkboxen + Ad-hoc) → Task 6 (`config.html`/`model_picker.js`). ✔
- M2 (ephemer, kein Zurückschreiben) → kein Config-Write irgendwo; Override nur in argv/Lauf. ✔
- M3 (ersetzt `config.models`) → `apply_models_override` via `model_copy(update={"models":…})` (Task 1/4). ✔
- M4 (ein Endpoint = der der Config) → Override ändert nur `models`, Endpoint bleibt; UI wählt eine Config. ✔
- M5 (JSON-Transport `--models-json`) → Task 3 (argv) + Task 4 (CLI) + Task 5 (Route). ✔
- M6 (Front-end: eingebettetes JSON + Alpine) → `models_by_config` (Task 2) + `model_picker.js` (Task 6). ✔
- M7 (resume → Picker aus + kein Override) → `{% if not resume %}` (Task 6) + `resume is None` Guard (Task 5). ✔
- §6 `models_json`-Semantik (leer→kein Override · `"[]"`/ungültig→Fehler kein Spawn) → `models_from_json` (≥1, raises) + Route-`HTTPException(400)` (Tasks 1/5). ✔ (Transport: 400 statt HTML-Re-render — bewusste, codebase-konforme Abweichung, oben notiert.)
- §7 Tests (config_models defensiv · override replace · start_eval-argv · Route valid/leer/ungültig/none · Render Picker + resume-aus) → Tasks 1/2/3/5/6. ✔
- §8 Dateien → alle abgedeckt; AGENTS.md Task 7. ✔
- §9 YAGNI (kein Zurückschreiben/Pool/Discovery) → nicht implementiert. ✔

**Placeholder scan:** Jeder Code-Step vollständig; keine TODO/"handle edge cases". ✔
**Type consistency:** `models_from_json(s)->list[ModelSpec]`, `apply_models_override(cfg,s)->Config`, `config_models(path)->list[ModelSpec]`, `models_by_config(files)->dict[str,list[dict]]`, `start_eval(...,models=None)`, Route-Feld `models_json`, Template-Key `models_by_config`, Alpine `modelPicker(byConfig)` — durchgängig konsistent. ✔
