// ramcheck/gui/static/model_picker.js
// Alpine component for the Konfig+Start model picker. Given {config_path: [model,...]},
// it shows the selected config's models as checkboxes (+ ad-hoc id/quant rows) and keeps
// a hidden models_json field in sync. Build-free; registered on alpine:init.
"use strict";

document.addEventListener("alpine:init", () => {
  Alpine.data("modelPicker", (byConfig, configs) => ({
    byConfig: byConfig,
    // Default to the first ORDERED config (configs is order_configs()-sorted, embed/vlm last).
    // Don't rely on byConfig key order — that would depend on JSON preserving insertion order.
    config: (configs && configs[0]) || Object.keys(byConfig)[0] || "",
    models: [],
    adhoc: [],
    _nextK: 0, // monotonic key so x-for rows stay stable across removals
    endpointModels: [],
    endpointError: "",
    endpointLoading: false,
    endpointPick: "",
    pickNote: "",
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
      const cfg = this.config; // guard: ignore a stale response for a superseded config
      this.endpointLoading = true;
      this.endpointError = "";
      this.endpointModels = [];
      this.endpointPick = "";
      this.pickNote = "";
      try {
        const res = await fetch("/endpoint-models?config=" + encodeURIComponent(cfg));
        const data = await res.json();
        if (this.config !== cfg) return; // a newer config selection superseded this request
        this.endpointModels = data.models || [];
        this.endpointError = data.error || "";
      } catch (e) {
        if (this.config === cfg) this.endpointError = "Endpoint-Abfrage fehlgeschlagen";
      } finally {
        if (this.config === cfg) this.endpointLoading = false;
      }
    },
    addFromEndpoint() {
      const id = (this.endpointPick || "").trim();
      this.pickNote = "";
      if (!id) return;
      // Skip if already selected (checked config model OR an existing ad-hoc row). Adding it
      // again would make a duplicate eval cell: config models carry a quant but endpoint adds
      // use quant="", so the server-side (id,quant) de-dupe would NOT collapse the two.
      const already =
        this.models.some((m) => m.on && m.id === id) ||
        this.adhoc.some((a) => a.id.trim() === id);
      if (already) {
        this.pickNote = id + " ist bereits ausgewählt";
        this.endpointPick = "";
        return;
      }
      this.adhoc.push({ id: id, quant: "", k: this._nextK++ });
      this.endpointPick = "";
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
