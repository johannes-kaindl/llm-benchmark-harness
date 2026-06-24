// touchstone/gui/static/scatter.js
/**
 * scatter.js — build-free inline-SVG efficiency scatter for the Modell-Vergleich.
 *
 * x = Decode tok/s, y = Qualität %, r = Peak-RAM (sys_used_mb). One point per axis
 * value. Reads svg[data-scatter] with data-points = JSON [{label,x,y,r}, ...].
 * Mirrors sparkline.js (vanilla DOM, no deps, DOMContentLoaded wiring).
 */
"use strict";

(function () {
  var SVGNS = "http://www.w3.org/2000/svg";

  function el(name, attrs) {
    var e = document.createElementNS(SVGNS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function renderScatter(svg) {
    var pts = [];
    try { pts = JSON.parse(svg.dataset.points || "[]"); } catch (e) {}
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (pts.length === 0) return;

    var W = parseFloat(svg.getAttribute("width") || "320");
    var H = parseFloat(svg.getAttribute("height") || "220");
    var PAD = 38;

    var xs = pts.map(function (p) { return p.x; });
    var xMin = Math.min.apply(null, xs), xMax = Math.max.apply(null, xs);
    if (xMin === xMax) { xMin -= 1; xMax += 1; }
    var yMin = 0, yMax = 100;                              // quality is a fixed 0..100 %
    var rs = pts.map(function (p) { return p.r || 0; });
    var rMax = Math.max.apply(null, rs) || 1;

    function sx(x) { return PAD + (x - xMin) / (xMax - xMin) * (W - 2 * PAD); }
    function sy(y) { return (H - PAD) - (y - yMin) / (yMax - yMin) * (H - 2 * PAD); }
    function sr(r) { return 5 + (r / rMax) * 13; }         // 5..18 px radius

    // axes
    svg.appendChild(el("line", { x1: PAD, y1: H - PAD, x2: W - PAD, y2: H - PAD, stroke: "#d4d4d8", "stroke-width": 1 }));
    svg.appendChild(el("line", { x1: PAD, y1: PAD, x2: PAD, y2: H - PAD, stroke: "#d4d4d8", "stroke-width": 1 }));
    var xlab = el("text", { x: W / 2, y: H - 6, "text-anchor": "middle", "font-size": 10, fill: "#71717a" });
    xlab.textContent = "Decode tok/s →"; svg.appendChild(xlab);
    var ylab = el("text", { x: 10, y: PAD - 10, "font-size": 10, fill: "#71717a" });
    ylab.textContent = "Qualität %"; svg.appendChild(ylab);

    pts.forEach(function (p) {
      var cx = sx(p.x), cy = sy(p.y);
      svg.appendChild(el("circle", {
        cx: cx, cy: cy, r: sr(p.r || 0),
        fill: "rgba(37,99,235,0.25)", stroke: "#2563eb", "stroke-width": 1.5,
      }));
      var t = el("text", { x: cx, y: cy - sr(p.r || 0) - 3, "text-anchor": "middle", "font-size": 10, fill: "#18181b" });
      t.textContent = p.label; svg.appendChild(t);
    });
  }

  function initScatter() {
    var svgs = document.querySelectorAll("svg[data-scatter]");
    for (var i = 0; i < svgs.length; i++) renderScatter(svgs[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initScatter);
  } else {
    initScatter();
  }
})();
