// touchstone/gui/static/pack_editor.js
// Alpine component for the guided pack editor. A YAML textarea + server-side validation (the one
// pydantic contract, no JS duplicate), a live preview reusing the viewer body, a verbatim download,
// and a guarded save into packs/. Build-free; registered on alpine:init, loaded non-deferred.
"use strict";

document.addEventListener("alpine:init", () => {
  Alpine.data("packEditor", (initialYaml, path) => ({
    yamlText: initialYaml || "",
    path: path || null,
    ok: null, // null = not yet validated / stale after an edit; true/false after a validate
    errors: [],
    summary: null,
    previewHtml: "",
    validating: false,
    filename: "",
    saving: false,
    saveError: "",
    saved: "",
    showOverwrite: false,
    init() {
      // prefill the save filename from the loaded path's stem (e.g. packs/ndassist.yaml → ndassist)
      if (this.path) {
        const base = this.path.split("/").pop() || "";
        this.filename = base.replace(/\.yaml$/, "");
      }
      this.validate(); // validate-on-load so the preview shows immediately
    },
    async validate() {
      this.validating = true;
      this.saved = "";
      this.showOverwrite = false;
      try {
        const res = await fetch("/packs/validate", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ yaml_text: this.yamlText }),
        });
        const data = await res.json();
        this.ok = data.ok;
        this.errors = data.errors || [];
        this.summary = data.summary;
        this.previewHtml = data.preview_html || "";
      } catch (e) {
        this.ok = false;
        this.errors = [{ loc: "(netz)", msg: "Validierung fehlgeschlagen" }];
        this.previewHtml = "";
      } finally {
        this.validating = false;
      }
    },
    summaryText() {
      const s = this.summary;
      if (!s) return "";
      return (
        "· " +
        s.prompts +
        " Prompts · " +
        s.dimensions +
        " Dim · " +
        s.variants +
        " Varianten · max " +
        s.max_weighted +
        " Punkte"
      );
    },
    download() {
      // verbatim: the textarea text, comments/anchors/order preserved (not the normalized dump)
      const blob = new Blob([this.yamlText], { type: "application/x-yaml" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = (this.filename.trim() || "pack") + ".yaml";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
    async save(overwrite) {
      this.saveError = "";
      this.saved = "";
      this.showOverwrite = false;
      // the server re-validates too (authoritative), but gate here for a clear UX
      if (this.ok !== true) {
        this.saveError = "Erst validieren — nur ein gültiges Pack wird gespeichert.";
        return;
      }
      if (!this.filename.trim()) {
        this.saveError = "Dateiname fehlt.";
        return;
      }
      this.saving = true;
      try {
        const body = new URLSearchParams({
          filename: this.filename.trim(),
          yaml_text: this.yamlText,
        });
        if (overwrite) body.set("overwrite", "true");
        const res = await fetch("/packs/save", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body,
        });
        if (res.status === 409) {
          this.showOverwrite = true; // existing file — ask for explicit overwrite
          return;
        }
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          this.saveError = d.detail || "Speichern fehlgeschlagen (" + res.status + ")";
          return;
        }
        const data = await res.json();
        this.saved = data.saved;
      } catch (e) {
        this.saveError = "Speichern fehlgeschlagen";
      } finally {
        this.saving = false;
      }
    },
  }));
});
