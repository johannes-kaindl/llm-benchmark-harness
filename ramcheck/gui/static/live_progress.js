// live_progress.js — native-EventSource live progress for a running run card.
//
// The htmx SSE extension is NOT bundled (htmx core dropped SSE in 2.0), so `hx-ext="sse"`
// was a no-op and the card sat at 0/0. We consume the server's `view` SSE events directly
// via the browser's native EventSource. Loaded NON-deferred so the Alpine component is
// registered before the (deferred) Alpine starts — same constraint as model_picker.js.
"use strict";

document.addEventListener("alpine:init", () => {
  Alpine.data("liveProgress", (name, kind) => ({
    name: name,
    kind: kind || "eval",
    done: 0,
    total: 0,
    pct: 0,
    ok: 0,
    failed: 0,
    _es: null,
    start() {
      try {
        this._es = new EventSource(
          "/live/" + encodeURIComponent(this.name) + "?kind=" + encodeURIComponent(this.kind),
        );
      } catch (e) {
        return;
      }
      this._es.addEventListener("view", (ev) => {
        let d;
        try {
          d = JSON.parse(ev.data);
        } catch (e) {
          return;
        }
        this.total = d.total || 0;
        this.done = d.done || 0;
        this.ok = d.ok || 0;
        this.failed = d.failed || 0;
        this.pct = this.total ? Math.round((this.done / this.total) * 100) : 0;
        if (d.finished && this._es) this._es.close();
      });
    },
    destroy() {
      if (this._es) this._es.close();
    },
  }));
});
