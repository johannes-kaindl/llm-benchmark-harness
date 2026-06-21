/**
 * sparkline.js — build-free inline-SVG sparkline for RAM/CPU time series.
 *
 * Usage: add `data-sparkline` to any <svg> element; its `data-ram` / `data-cpu`
 * attributes carry JSON arrays ([number|null, ...]).  null values are treated as
 * gaps (no line drawn) so old bundles without CPU data show no CPU trace.
 *
 * Wired by initSparklines() called on DOMContentLoaded.
 */

"use strict";

(function () {
  /** Return a polyline path-string for a series of [x,y] points, skipping nulls. */
  function polylinePath(xs, ys) {
    if (!xs || xs.length === 0) return "";
    var segments = [];
    var inSeg = false;
    for (var i = 0; i < xs.length; i++) {
      if (ys[i] == null || isNaN(ys[i])) {
        inSeg = false;
        continue;
      }
      if (!inSeg) {
        segments.push("M" + xs[i].toFixed(1) + "," + ys[i].toFixed(1));
        inSeg = true;
      } else {
        segments.push("L" + xs[i].toFixed(1) + "," + ys[i].toFixed(1));
      }
    }
    return segments.join(" ");
  }

  /** Normalise a series to [0, height] range, returns null-preserved float array. */
  function normalise(vals, height, padPx) {
    var nums = vals.filter(function (v) { return v != null && !isNaN(v); });
    if (nums.length === 0) return vals.map(function () { return null; });
    var min = Math.min.apply(null, nums);
    var max = Math.max.apply(null, nums);
    var range = max - min;
    return vals.map(function (v) {
      if (v == null || isNaN(v)) return null;
      var frac = range > 0 ? (v - min) / range : 0.5;
      // top is y=padPx, bottom is y=(height-padPx): invert frac
      return (height - padPx) - frac * (height - 2 * padPx);
    });
  }

  function renderSparkline(svg) {
    var ramRaw = [];
    var cpuRaw = [];
    try { ramRaw = JSON.parse(svg.dataset.ram || "[]"); } catch (e) {}
    try { cpuRaw = JSON.parse(svg.dataset.cpu || "[]"); } catch (e) {}

    var W = parseFloat(svg.getAttribute("width") || "200");
    var H = parseFloat(svg.getAttribute("height") || "40");
    var PAD = 3;

    var maxLen = Math.max(ramRaw.length, cpuRaw.length, 1);
    var xs = ramRaw.length > 0
      ? ramRaw.map(function (_, i) { return PAD + (i / (ramRaw.length - 1 || 1)) * (W - 2 * PAD); })
      : [PAD, W - PAD];

    var cpuXs = cpuRaw.length > 0
      ? cpuRaw.map(function (_, i) { return PAD + (i / (cpuRaw.length - 1 || 1)) * (W - 2 * PAD); })
      : [PAD, W - PAD];

    var ramYs = normalise(ramRaw, H, PAD);
    var cpuYs = normalise(cpuRaw, H, PAD);

    // Clear existing content
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    // RAM line (blue)
    var ramPath = polylinePath(xs, ramYs);
    if (ramPath) {
      var ramEl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      var polyEl = document.createElementNS("http://www.w3.org/2000/svg", "path");
      polyEl.setAttribute("d", ramPath);
      polyEl.setAttribute("fill", "none");
      polyEl.setAttribute("stroke", "#2563eb");
      polyEl.setAttribute("stroke-width", "1.5");
      polyEl.setAttribute("stroke-linejoin", "round");
      polyEl.setAttribute("stroke-linecap", "round");
      svg.appendChild(polyEl);
    }

    // CPU line (orange), only when data is present (no all-null series)
    var cpuPath = polylinePath(cpuXs, cpuYs);
    if (cpuPath) {
      var cpuEl = document.createElementNS("http://www.w3.org/2000/svg", "path");
      cpuEl.setAttribute("d", cpuPath);
      cpuEl.setAttribute("fill", "none");
      cpuEl.setAttribute("stroke", "#d97706");
      cpuEl.setAttribute("stroke-width", "1.5");
      cpuEl.setAttribute("stroke-linejoin", "round");
      cpuEl.setAttribute("stroke-linecap", "round");
      svg.appendChild(cpuEl);
    }

    // Max / Ø labels injected into sibling [data-sparkline-labels]
    var labelsEl = svg.parentElement && svg.parentElement.querySelector("[data-sparkline-labels]");
    if (labelsEl) {
      var ramNums = ramRaw.filter(function (v) { return v != null && !isNaN(v); });
      var cpuNums = cpuRaw.filter(function (v) { return v != null && !isNaN(v); });
      var parts = [];
      if (ramNums.length > 0) {
        var ramMax = (Math.max.apply(null, ramNums) / 1024).toFixed(1);
        var ramAvg = (ramNums.reduce(function (a, b) { return a + b; }, 0) / ramNums.length / 1024).toFixed(1);
        parts.push("RAM Max " + ramMax + " GB · Ø " + ramAvg + " GB");
      }
      if (cpuNums.length > 0) {
        var cpuMax = Math.max.apply(null, cpuNums).toFixed(0);
        var cpuAvg = (cpuNums.reduce(function (a, b) { return a + b; }, 0) / cpuNums.length).toFixed(0);
        parts.push("CPU Max " + cpuMax + "% · Ø " + cpuAvg + "%");
      }
      labelsEl.textContent = parts.join(" · ");
    }
  }

  function initSparklines() {
    var svgs = document.querySelectorAll("svg[data-sparkline]");
    for (var i = 0; i < svgs.length; i++) {
      renderSparkline(svgs[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSparklines);
  } else {
    initSparklines();
  }
})();
