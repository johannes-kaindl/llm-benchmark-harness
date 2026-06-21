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
