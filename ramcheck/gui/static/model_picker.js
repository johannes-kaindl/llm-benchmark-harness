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
    _nextK: 0, // monotonic key so x-for rows stay stable across removals
    endpointModels: [],
    endpointError: "",
    endpointLoading: false,
    endpointPick: "",
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
});
