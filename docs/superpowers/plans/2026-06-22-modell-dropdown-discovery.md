# Modell-Dropdown via Endpoint-Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Im Modell-Picker ein Dropdown der am Endpoint der gewählten Config tatsächlich verfügbaren Modelle (`/v1/models`, server-seitig abgefragt) anbieten; Pick → der Lauf-Auswahl hinzufügen. Plus: Default-Config nicht mehr die embed-Config.

**Architecture:** `client.py` bekommt `list_models()` (+ optionalen Timeout) — die einzige engine-bewusste Stelle. `configs.py` bekommt `discover_endpoint_models` (DI-Lister → netzfrei testbar, fängt alles → `{"models","error"}`) und `order_configs` (embed/vlm hinten). Eine Route `GET /endpoint-models` liefert das JSON; der Alpine-Picker holt es per `fetch` bei Config-Wechsel und füllt ein `<select>`, „Hinzufügen" legt eine Auswahl-Zeile an. Kein Eingriff in `/runs/eval`/`models_json`.

**Tech Stack:** Python 3.12 · pydantic v2 · OpenAI-SDK (`models.list()`) · FastAPI · Jinja2 · Alpine (build-frei) · pytest (`uv run pytest`) · headless Chrome (`--dump-dom`) für JS-Verifikation.

---

## Pre-flight

- **Working dir:** `/Users/Shared/code/llm-benchmark-harness`; Tests via `uv run pytest` aus dem Repo-Root.
- **Gates je Task:** jeweilige Testdatei; am Ende (Task 5): `uv run pytest -q`, `uv run mypy ramcheck`, `uv run ruff check ramcheck tests`, `uv run ruff format --check ramcheck tests`.
- **Trailer:** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. **Branch:** `feat/modell-dropdown-discovery`. Kein Push (Controller am Ende).
- **JS-Bug-Lehre:** TestClient prüft nur Markup, nicht laufendes JS — die echte JS-Verifikation ist der headless-Chrome-Smoke in Task 5. `model_picker.js` bleibt **nicht-deferred** geladen (sonst registriert sich `modelPicker` zu spät, der Picker ist tot).

## Verifizierte Fakten

- `ramcheck/client.py`: `OpenAIStreamClient(base_url: str, api_key: str = "not-needed", *, engine="openai-compat", engine_version="unknown")`; `self._client = OpenAI(base_url=base_url, api_key=api_key)` (lazy, kein Connect beim Bau). SDK: `self._client.models.list().data` → Elemente mit `.id`.
- `ramcheck/config.py`: `Endpoint(base_url, api_key="not-needed")`, `Config.endpoint`, `load_config(path) -> Config`.
- `ramcheck/gui/configs.py`: hat `config_models`, `models_by_config`; importiert `from ramcheck.config import ModelSpec` (+ `ValidationError`).
- `ramcheck/gui/app.py`: `/config`-Route baut `config_files = sorted(str(p) for p in Path(".").glob("config*.yaml"))`, importiert `from ramcheck.gui import bundles, compare, configs as configs_mod` (configs_mod ist vorhanden — aus dem Picker-Feature) und nutzt `_confine_cwd`. `/runs/eval` unverändert.
- `model_picker.js`: Alpine-Komponente `modelPicker(byConfig)` mit `config`, `models`, `adhoc`, `_nextK`, `syncFromConfig()`, `addAdhoc()`, `removeAdhoc(k)`, `count()`, `modelsJson()`. In `config.html` **nicht-deferred** eingebunden; `x-data='modelPicker({{ models_by_config | tojson }})'`.
- Test-Muster: `gui_app.create_app(runs_dir=tmp_path, registry=RunRegistry(runs_dir=tmp_path, launcher=Fake))` + `TestClient`. Reale `config*.yaml` liegen im cwd.

## File Structure

| Datei | Verantwortung |
|---|---|
| `ramcheck/client.py` *(ändern)* | `list_models()` + optionaler `timeout` |
| `ramcheck/gui/configs.py` *(ändern)* | `discover_endpoint_models` (DI) + `order_configs` |
| `ramcheck/gui/app.py` *(ändern)* | Route `GET /endpoint-models`; `/config` nutzt `order_configs` |
| `ramcheck/gui/static/model_picker.js` *(ändern)* | Discovery-State + `fetchEndpointModels` + `addFromEndpoint` |
| `ramcheck/gui/templates/config.html` *(ändern)* | Endpoint-Dropdown + „Hinzufügen" + Status |
| `tests/test_client_models.py` *(neu)* | `list_models` |
| `tests/test_gui_configs.py` *(ändern)* | `discover_endpoint_models` + `order_configs` |
| `tests/test_gui_endpoint_models.py` *(neu)* | Route + `/config`-Ordering + Markup |
| `AGENTS.md` *(ändern)* | Notiz zur Endpoint-Discovery |

---

## Task 1: `client.py` — `list_models()` + `timeout`

**Files:**
- Modify: `ramcheck/client.py`
- Test: `tests/test_client_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client_models.py
from __future__ import annotations

import types

from ramcheck.client import OpenAIStreamClient


def test_list_models_returns_ids():
    c = OpenAIStreamClient("http://localhost:1/v1")  # OpenAI() is lazy, no connection
    c._client = types.SimpleNamespace(
        models=types.SimpleNamespace(
            list=lambda: types.SimpleNamespace(
                data=[types.SimpleNamespace(id="a"), types.SimpleNamespace(id="b")]
            )
        )
    )
    assert c.list_models() == ["a", "b"]


def test_constructor_accepts_timeout():
    c = OpenAIStreamClient("http://localhost:1/v1", timeout=3.0)
    assert c._client is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client_models.py -q`
Expected: FAIL — `OpenAIStreamClient.__init__() got an unexpected keyword argument 'timeout'` / no `list_models`.

- [ ] **Step 3: Write minimal implementation**

In `ramcheck/client.py`, change `__init__` to accept `timeout` and add `list_models`:

```python
    def __init__(
        self,
        base_url: str,
        api_key: str = "not-needed",
        *,
        engine: str = "openai-compat",
        engine_version: str = "unknown",
        timeout: float | None = None,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.engine = engine
        self.engine_version = engine_version

    def list_models(self) -> list[str]:
        """Model ids the endpoint advertises at /v1/models (for the GUI picker dropdown)."""
        return [m.id for m in self._client.models.list().data]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client_models.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/client.py tests/test_client_models.py
git commit -m "feat(client): list_models() + optional timeout (endpoint model discovery)"
```

---

## Task 2: `configs.py` — `discover_endpoint_models` + `order_configs`

**Files:**
- Modify: `ramcheck/gui/configs.py`
- Test: `tests/test_gui_configs.py`

- [ ] **Step 1: Write the failing test (append to tests/test_gui_configs.py)**

```python
# append to tests/test_gui_configs.py
def test_discover_endpoint_models_success():
    out = configs.discover_endpoint_models("config.m5.yaml", lister=lambda: ["m1", "m2"])
    assert out == {"models": ["m1", "m2"], "error": None}


def test_discover_endpoint_models_error_no_throw():
    def boom():
        raise ConnectionError("connection refused")

    out = configs.discover_endpoint_models("config.m5.yaml", lister=boom)
    assert out["models"] == []
    assert "refused" in out["error"]


def test_order_configs_puts_embed_and_vlm_last():
    got = configs.order_configs(
        ["config.embed.yaml", "config.m5.yaml", "config.x.vlm.yaml", "config.a.yaml"]
    )
    assert got == ["config.a.yaml", "config.m5.yaml", "config.embed.yaml", "config.x.vlm.yaml"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_configs.py -k "discover or order_configs" -q`
Expected: FAIL — `module 'ramcheck.gui.configs' has no attribute 'discover_endpoint_models'`.

- [ ] **Step 3: Write minimal implementation**

In `ramcheck/gui/configs.py`, add imports and the two functions:

```python
from collections.abc import Callable

from ramcheck.config import ModelSpec, load_config  # extend the existing import line


def order_configs(paths: list[str]) -> list[str]:
    """Sort config paths so *embed*/*vlm* files sort last (the picker shouldn't default to
    the embedding config). Within each group, alphabetical."""

    def key(p: str) -> tuple[bool, str]:
        name = Path(p).name.lower()
        return (("embed" in name or "vlm" in name), p)

    return sorted(paths, key=key)


def discover_endpoint_models(
    config_path: str | Path,
    *,
    lister: Callable[[], list[str]] | None = None,
) -> dict[str, Any]:
    """{"models": [ids...], "error": str|None}. NEVER raises — a dead/slow endpoint or a
    broken config yields an empty list + an error string so the page/route never breaks."""
    if lister is None:

        def lister() -> list[str]:
            from ramcheck.client import OpenAIStreamClient

            cfg = load_config(config_path)
            client = OpenAIStreamClient(
                cfg.endpoint.base_url, cfg.endpoint.api_key, timeout=3.0
            )
            return client.list_models()

    try:
        models = lister()
    except Exception as e:  # noqa: BLE001 — any failure degrades to an error message
        return {"models": [], "error": f"Endpoint nicht erreichbar: {e}"}
    # de-dupe, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for m in models:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return {"models": out, "error": None}
```

> Note: the existing top import is `from ramcheck.config import ModelSpec` — change it to `from ramcheck.config import ModelSpec, load_config`. Add `from collections.abc import Callable` near the other imports. `Any` is already imported (used by `models_by_config`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_configs.py -q`
Expected: PASS (all configs tests).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/configs.py tests/test_gui_configs.py
git commit -m "feat(gui): discover_endpoint_models (DI, never raises) + order_configs (embed/vlm last)"
```

---

## Task 3: `/endpoint-models` route + `/config` ordering

**Files:**
- Modify: `ramcheck/gui/app.py`
- Test: `tests/test_gui_endpoint_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_endpoint_models.py
from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui import configs as configs_mod
from ramcheck.gui.control import RunRegistry


class _FakeLauncher:
    def spawn(self, argv):
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def test_endpoint_models_route_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        configs_mod, "discover_endpoint_models", lambda config: {"models": ["x", "y"], "error": None}
    )
    r = _client(tmp_path).get("/endpoint-models?config=config.m5.yaml")
    assert r.status_code == 200
    assert r.json() == {"models": ["x", "y"], "error": None}


def test_endpoint_models_route_error_is_200(tmp_path, monkeypatch):
    monkeypatch.setattr(
        configs_mod, "discover_endpoint_models", lambda config: {"models": [], "error": "down"}
    )
    r = _client(tmp_path).get("/endpoint-models?config=config.m5.yaml")
    assert r.status_code == 200  # offline endpoint is not a server error
    assert r.json()["error"] == "down"


def test_endpoint_models_route_rejects_traversal(tmp_path):
    assert _client(tmp_path).get("/endpoint-models?config=../etc/passwd").status_code == 404


def test_config_default_is_not_embed(tmp_path):
    body = _client(tmp_path).get("/config").text
    m = re.search(r"modelPicker\((\{.*?\})\)", body)
    assert m is not None
    first_key = next(iter(json.loads(m.group(1)).keys()))
    assert "embed" not in first_key and "vlm" not in first_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_endpoint_models.py -q`
Expected: FAIL — `/endpoint-models` 404 (route missing); default-config still embed.

- [ ] **Step 3: Write minimal implementation**

In `ramcheck/gui/app.py` `config_get`, replace the `config_files = sorted(...)` line:

```python
        config_files = configs_mod.order_configs(
            [str(p) for p in Path(".").glob("config*.yaml")]
        )
```

Add the route (near the other `@app.get` routes, e.g. after the `/config` route):

```python
    @app.get("/endpoint-models")
    def endpoint_models(config: str) -> dict[str, Any]:
        """Models the selected config's endpoint advertises (/v1/models). Never 500s —
        a dead endpoint returns {"models": [], "error": "..."}."""
        config = _confine_cwd(config)
        return configs_mod.discover_endpoint_models(config)
```

> `_confine_cwd` is defined in `_register_control_routes`; if the new route is added in `create_app` (where `/config` lives), define/duplicate the cwd guard there. Mirror the `/packs` guard exactly: reject absolute or `..` paths → `HTTPException(404)`. If a module-level `_confine_cwd` is not in scope at the `/config` route, inline:
> ```python
>         candidate = Path(config)
>         if candidate.is_absolute() or ".." in candidate.parts:
>             raise HTTPException(status_code=404)
> ```
> (Use whichever matches where `/config` is defined — keep it consistent with the existing `/runs/eval` confinement.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_endpoint_models.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/app.py tests/test_gui_endpoint_models.py
git commit -m "feat(gui): /endpoint-models route + non-embed default config ordering"
```

---

## Task 4: Picker dropdown — `model_picker.js` + `config.html`

**Files:**
- Modify: `ramcheck/gui/static/model_picker.js`
- Modify: `ramcheck/gui/templates/config.html`
- Test: `tests/test_gui_endpoint_models.py` (append markup assertions)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_gui_endpoint_models.py
def test_config_page_has_endpoint_dropdown(tmp_path):
    body = _client(tmp_path).get("/config").text
    assert "endpointPick" in body          # dropdown bound to component state
    assert "fetchEndpointModels" not in body  # JS lives in model_picker.js, not inline
    assert "Vom Endpoint" in body          # section label
    assert "Hinzufügen" in body            # add button


def test_config_page_endpoint_dropdown_hidden_on_resume(tmp_path):
    body = _client(tmp_path).get("/config?resume=foo").text
    assert "endpointPick" not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gui_endpoint_models.py -k endpoint_dropdown -q`
Expected: FAIL — no `endpointPick`/`Vom Endpoint` in the page.

- [ ] **Step 3a: Extend `model_picker.js`**

Add the discovery state and methods (extend the returned object; keep existing keys):

```javascript
  Alpine.data("modelPicker", (byConfig) => ({
    byConfig: byConfig,
    config: Object.keys(byConfig)[0] || "",
    models: [],
    adhoc: [],
    _nextK: 0,
    endpointModels: [],
    endpointError: "",
    endpointLoading: false,
    endpointPick: "",
    init() {
      this.syncFromConfig();
    },
    syncFromConfig() {
      const list = this.byConfig[this.config] || [];
      this.models = list.map((m) => ({
        id: m.id,
        quant: m.quant || "",
        max_tokens_default: m.max_tokens_default || 400,
        on: true,
      }));
      this.adhoc = [];
      this.fetchEndpointModels(); // async, fire-and-forget
    },
    async fetchEndpointModels() {
      this.endpointLoading = true;
      this.endpointError = "";
      this.endpointModels = [];
      this.endpointPick = "";
      try {
        const res = await fetch("/endpoint-models?config=" + encodeURIComponent(this.config));
        const data = await res.json();
        this.endpointModels = data.models || [];
        this.endpointError = data.error || "";
      } catch (e) {
        this.endpointError = "Endpoint-Abfrage fehlgeschlagen";
      } finally {
        this.endpointLoading = false;
      }
    },
    addFromEndpoint() {
      const id = (this.endpointPick || "").trim();
      if (id) {
        this.adhoc.push({ id: id, quant: "", k: this._nextK++ });
        this.endpointPick = "";
      }
    },
    addAdhoc() {
      this.adhoc.push({ id: "", quant: "", k: this._nextK++ });
    },
    removeAdhoc(k) {
      this.adhoc = this.adhoc.filter((a) => a.k !== k);
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
```

- [ ] **Step 3b: Add the dropdown to `config.html`**

Inside the Modelle `<div class="form-group">` (the `{% if not resume %}` block), after the ad-hoc rows / „+ Modell" button and before the hidden `models_json` input, insert:

```html
        <div style="margin-top:0.6rem">
          <label class="form-label text-xs" style="display:block">Vom Endpoint hinzufügen</label>
          <div class="flex gap-2">
            <select class="form-select" x-model="endpointPick" :disabled="endpointModels.length === 0" style="flex:1">
              <option value="">— Modell wählen —</option>
              <template x-for="mid in endpointModels" :key="mid">
                <option :value="mid" x-text="mid"></option>
              </template>
            </select>
            <button type="button" class="btn btn-secondary" @click="addFromEndpoint()" :disabled="!endpointPick">Hinzufügen</button>
          </div>
          <p class="muted text-xs" x-show="endpointLoading" style="margin-top:0.2rem">… Endpoint wird abgefragt</p>
          <p class="muted text-xs" x-show="endpointError" x-text="endpointError" style="color:var(--einschr); margin-top:0.2rem"></p>
        </div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gui_endpoint_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ramcheck/gui/static/model_picker.js ramcheck/gui/templates/config.html tests/test_gui_endpoint_models.py
git commit -m "feat(gui): endpoint-model dropdown in the picker (fetch /endpoint-models, add to selection)"
```

---

## Task 5: AGENTS.md + gates + headless-Chrome-Smoke

**Files:**
- Modify: `AGENTS.md`
- Verify: full suite + static gates + headless JS smoke

- [ ] **Step 1: Update AGENTS.md**

Add to the GUI/control section:

```markdown
- **Endpoint-Modell-Discovery:** Der Picker fragt beim Config-Wechsel `GET /endpoint-models?config=…`
  ab; der Server ruft `/v1/models` des Config-Endpoints (`OpenAIStreamClient.list_models`, Timeout ~3 s)
  und liefert `{"models": [...], "error": null}` — **nie 500**, ein toter Endpoint ergibt `error` +
  leere Liste. Das Dropdown füllt sich daraus; „Hinzufügen" legt eine Auswahl-Zeile an. Default-Config
  ordnet `*embed*`/`*vlm*` nach hinten (`order_configs`). Pure Logik: `configs.discover_endpoint_models`.
```

- [ ] **Step 2: Full suite + static gates**

```bash
uv run pytest -q          # expect green (baseline 298 + new)
uv run mypy ramcheck
uv run ruff check ramcheck tests
uv run ruff format --check ramcheck tests   # if it reformats: run `uv run ruff format ramcheck tests`, re-stage
```

- [ ] **Step 3: Headless-Chrome JS-Smoke (Pflicht — verifiziert laufendes JS)**

Run a real browser against a GUI whose `/endpoint-models` is forced to return models, and confirm the dropdown renders `<option>`s (proves the extended Alpine component initializes + the fetch path wires through). Use this exact procedure:

```bash
# 1) tiny app override that stubs discovery (so the smoke doesn't need a live endpoint)
cat > /tmp/smoke_disc.py <<'PY'
from pathlib import Path
from ramcheck.gui import app as gui_app, configs as cm
from ramcheck.gui.control import RunRegistry, RealProcessLauncher
cm.discover_endpoint_models = lambda config: {"models": ["smoke-model-A", "smoke-model-B"], "error": None}
app = gui_app.create_app(runs_dir=Path("runs"), registry=RunRegistry(runs_dir=Path("runs"), launcher=RealProcessLauncher()))
PY
uv run uvicorn --app-dir /tmp smoke_disc:app --port 8791 > /tmp/smoke_disc.log 2>&1 &
curl -s --retry 25 --retry-delay 1 --retry-connrefused --max-time 3 -o /dev/null http://127.0.0.1:8791/ && echo UP
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless --disable-gpu --no-sandbox --virtual-time-budget=3000 --dump-dom http://127.0.0.1:8791/config > /tmp/dom_disc.html 2>/dev/null
grep -c "smoke-model-A" /tmp/dom_disc.html   # expect >=1 (rendered <option>, proves fetch+Alpine worked)
grep -o '<option[^>]*>smoke-model-A</option>' /tmp/dom_disc.html | head
pkill -9 -f "8791"
```
Expected: `smoke-model-A` appears as a rendered `<option>` → the dropdown is populated by the live fetch. If 0, the JS/fetch wiring is broken — fix before merge.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): endpoint model discovery (/endpoint-models, /v1/models, never 500)"
```

- [ ] **Step 5: Manual (für Johannes)**

GUI neu starten → `/config` → Default ist eine Chat-Config → Dropdown listet die ollama/LM-Studio-Modelle → eines wählen + „Hinzufügen" → erscheint als Zeile → „Eval starten". Endpoint aus → Hinweis, Ad-hoc bleibt.

---

## Self-Review (gegen die Spec)

**Spec coverage:** D1 Discovery vom Endpoint → Task 1 (`list_models`) + Task 2 (`discover_endpoint_models`) + Task 3 (Route) + Task 4 (fetch). D2 Dropdown→Auswahl → Task 4 (`addFromEndpoint` → adhoc row). D3 Timeout/nie-500 → Task 1 (timeout=3s) + Task 2 (catch-all) + Task 3 (200 on error). D4 Default-Config → Task 2 (`order_configs`) + Task 3 (`/config` nutzt es) + Test `test_config_default_is_not_embed`. D5 `list_models` in client.py + DI-Lister → Task 1/2. §6 Error-Handling → Tasks 2/3/4 + Tests. §7 Tests inkl. headless-Smoke → Tasks 1–5. §8 Dateien → alle. §9 YAGNI → kein Caching/Prebake/Judge-Discovery.

**Placeholder scan:** Jeder Code-Step vollständig; der einzige bewusste „wähle die passende Confine-Variante"-Hinweis in Task 3 ist konkretisiert (inline-Guard angegeben). 

**Type consistency:** `list_models(self) -> list[str]`; `discover_endpoint_models(config_path, *, lister=None) -> dict[str, Any]`; `order_configs(paths) -> list[str]`; Route `endpoint_models(config: str) -> dict`; JS `endpointModels/endpointError/endpointLoading/endpointPick`, `fetchEndpointModels`, `addFromEndpoint` — durchgängig konsistent zwischen Tasks + Template + Tests.
