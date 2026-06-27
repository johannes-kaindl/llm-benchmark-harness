// touchstone/gui/static/model_picker.js
// Alpine component for the Konfig+Start model picker. ONE model per run (multiple models in a
// single run would corrupt the RAM measurement — shared baseline + no unload inflates the 2nd+
// model's delta). A single <select> sourced from the endpoint's real /v1/models, merged with the
// config's declared models (which carry the thinking knobs). Emits a one-element models_json so
// the backend is unchanged and forward-compatible with a future sequential-runs queue.
"use strict";

document.addEventListener("alpine:init", () => {
  Alpine.data("modelPicker", (byConfig, configs, summaries) => ({
    byConfig: byConfig,
    summaries: summaries || {}, // {config_path: {machine, engine, base_url}} for the inline preview
    // Default to the first ORDERED config (configs is order_configs()-sorted, embed/vlm last).
    // Don't rely on byConfig key order — that would depend on JSON preserving insertion order.
    config: (configs && configs[0]) || Object.keys(byConfig)[0] || "",
    options: [],
    chosen: "",
    userChose: false, // true once the user picks a model (@change) — gates default adoption
    manualId: "",
    manualQuant: "",
    endpointError: "",
    endpointLoading: false,
    init() {
      this.syncFromConfig();
    },
    syncFromConfig() {
      // Offline-safe seed from the config's declared models (served=false) so the select is
      // never empty; the async fetch then refines with the endpoint's real list + default.
      const seed = (this.byConfig[this.config] || []).map((m) => ({
        id: m.id,
        quant: m.quant || "",
        max_tokens_default: m.max_tokens_default || 400,
        reasoning_headroom_tokens: m.reasoning_headroom_tokens || 0,
        extra_body: m.extra_body || {},
        served: false,
        source: "config",
      }));
      this.options = seed;
      this.chosen = seed.length ? seed[0].id : "__manual__";
      this.userChose = false; // a fresh config re-enables default adoption
      this.manualId = "";
      this.manualQuant = "";
      this.fetchModelOptions(); // async, fire-and-forget
    },
    async fetchModelOptions() {
      const cfg = this.config; // guard: ignore a stale response for a superseded config
      this.endpointLoading = true;
      this.endpointError = "";
      try {
        const res = await fetch("/eval-model-options?config=" + encodeURIComponent(cfg));
        const data = await res.json();
        if (this.config !== cfg) return; // a newer config selection superseded this request
        this.options = data.options || [];
        this.endpointError = data.error || "";
        // Adopt the server-computed default (prefers a real served model over a config
        // placeholder) — but NEVER clobber a model the user already picked in the fetch window.
        if (this.userChose) return;
        if (data.default_id) {
          this.chosen = data.default_id;
        } else if (!this.options.length) {
          this.chosen = "__manual__";
        }
      } catch (e) {
        if (this.config === cfg) this.endpointError = "Endpoint-Abfrage fehlgeschlagen";
      } finally {
        if (this.config === cfg) this.endpointLoading = false;
      }
    },
    servedCount() {
      return this.options.filter((o) => o.served).length;
    },
    summary() {
      // {machine, engine, base_url} of the selected config, for the inline preview line.
      return this.summaries[this.config] || null;
    },
    optionLabel(o) {
      if (!o.served) return o.id + " (nicht geladen)";
      return o.quant ? o.id + " · " + o.quant : o.id;
    },
    _selected() {
      return this.options.find((o) => o.id === this.chosen) || null;
    },
    count() {
      if (this.chosen === "__manual__") return this.manualId.trim() ? 1 : 0;
      return this._selected() ? 1 : 0;
    },
    modelsJson() {
      if (this.chosen === "__manual__") {
        const id = this.manualId.trim();
        if (!id) return "[]";
        return JSON.stringify([
          { id: id, quant: this.manualQuant.trim(), max_tokens_default: 400 },
        ]);
      }
      const o = this._selected();
      if (!o) return "[]";
      // Carry the config thinking knobs (reasoning_headroom_tokens / extra_body) for a fair compare.
      return JSON.stringify([
        {
          id: o.id,
          quant: o.quant,
          max_tokens_default: o.max_tokens_default,
          reasoning_headroom_tokens: o.reasoning_headroom_tokens || 0,
          extra_body: o.extra_body || {},
        },
      ]);
    },
  }));

  // Slim judge-model picker: fetches /judge-endpoint-models on judge-config change and fills a
  // single dropdown (the judge only needs an id — no quant/multi-select like the eval picker).
  Alpine.data("judgeModelPicker", () => ({
    judgeConfig: "",
    models: [],
    pick: "",
    loading: false,
    error: "",
    init() {
      const sel = document.getElementById("judge_config_path");
      this.judgeConfig = sel ? sel.value : "";
      this.fetchModels();
    },
    async fetchModels() {
      const cfg = this.judgeConfig; // guard: ignore a stale response for a superseded config
      if (!cfg) return;
      this.loading = true;
      this.error = "";
      this.models = [];
      this.pick = "";
      try {
        const res = await fetch("/judge-endpoint-models?judge_config=" + encodeURIComponent(cfg));
        const data = await res.json();
        if (this.judgeConfig !== cfg) return;
        this.models = data.models || [];
        this.error = data.error || "";
      } catch (e) {
        if (this.judgeConfig === cfg) this.error = "Endpoint-Abfrage fehlgeschlagen";
      } finally {
        if (this.judgeConfig === cfg) this.loading = false;
      }
    },
  }));
});
