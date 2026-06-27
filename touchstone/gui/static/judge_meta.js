// touchstone/gui/static/judge_meta.js
// Alpine component for the per-bundle judge-quality meta-eval card. A YAML textarea → POST
// /judge-meta-ingest/{name} → server validates (parse_meta_response), writes judge_quality.md,
// returns an inline summary. Never alerts; a parse/schema error is a normal {ok:false} result.
// Build-free; registered on alpine:init, loaded non-deferred.
"use strict";

document.addEventListener("alpine:init", () => {
  Alpine.data("judgeMeta", (name) => ({
    name: name,
    yamlText: "",
    ok: null, // null = not yet submitted; true/false after a POST
    errors: [],
    summaryHtml: "",
    downloadUrl: "",
    busy: false,
    async ingest() {
      this.busy = true;
      this.ok = null;
      this.errors = [];
      try {
        const res = await fetch(`/judge-meta-ingest/${encodeURIComponent(this.name)}`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ yaml_text: this.yamlText }),
        });
        if (!res.ok) {
          // 400/404 (oversize / unjudged / traversal) — surface the server detail.
          this.ok = false;
          const d = await res.json().catch(() => ({}));
          this.errors = [d.detail || `Fehler (${res.status})`];
          return;
        }
        const data = await res.json();
        this.ok = data.ok;
        this.errors = data.errors || [];
        this.summaryHtml = data.summary_html || "";
        this.downloadUrl = data.download_url || "";
      } catch (e) {
        this.ok = false;
        this.errors = ["Auswertung fehlgeschlagen (Netz)"];
      } finally {
        this.busy = false;
      }
    },
  }));
});
