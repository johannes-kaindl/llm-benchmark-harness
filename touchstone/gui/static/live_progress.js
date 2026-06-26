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
    preflight: [],
    finished: false,
    _es: null,
    _poll: null,
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
        // preflight is folded into the view by build_view (Task 8); show only non-ok models
        this.preflight = (d.preflight || []).filter((p) => p.status !== "ok");
        this.pct = this.total ? Math.round((this.done / this.total) * 100) : 0;
        if (d.finished && this._es) {
          this._es.close();
          this._es = null;
          this.finished = true;
          this._awaitFinalized();
        }
      });
    },
    _awaitFinalized() {
      // 'finished' fires at run_done — BEFORE the bundle is finalized and the sentinel marked
      // terminal. The status badge/card are server-rendered, so we reload the overview to
      // re-derive them — but only once the run is no longer active (condition, not a guessed
      // delay), else discover would still classify it 'running'.
      const tick = () => {
        fetch("/run-active/" + encodeURIComponent(this.name))
          .then((r) => r.json())
          .then((d) => {
            if (!d.active) {
              location.reload();
            } else {
              this._poll = setTimeout(tick, 800);
            }
          })
          .catch(() => location.reload()); // server gone/error: a reload is the safe fallback
      };
      tick();
    },
    destroy() {
      if (this._es) this._es.close();
      if (this._poll) clearTimeout(this._poll);
    },
  }));
});
