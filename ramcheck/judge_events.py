"""Judge view for the live monitor — the scoring counterpart to events.py.

The judge loop fires callbacks (on_judge_start / on_verdict / master / on_judge_done);
the CLI's --web wiring turns them into lines of an append-only judge_events.jsonl. The
monitor subprocess reads those lines back and aggregates them with build_view(). Pure:
no I/O beyond (de)serialising dicts. Unlike the eval view it does NOT tail resources.jsonl
(the judge runs on a different endpoint; the bundle's resources.jsonl is the old eval run).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterable
from dataclasses import dataclass, field

JUDGE_START = "judge_start"
VERDICT = "verdict"
MASTER = "master"
JUDGE_DONE = "judge_done"

TAILS_RESOURCES = False
RATIONALE_MAX = 160

INDEX_HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>ramcheck judge monitor</title>
<style>
 body{font-family:system-ui,sans-serif;margin:1.5rem;background:#111;color:#eee}
 h1{font-size:1.1rem} h2{font-size:1rem;margin-top:1.5rem}
 .bar{background:#333;border-radius:4px;height:1.2rem;overflow:hidden}
 .bar>div{background:#3a7;height:100%;width:0;transition:width .3s}
 .grid{display:flex;gap:1rem;margin:1rem 0;flex-wrap:wrap}
 .card{background:#1b1b1b;padding:.7rem 1rem;border-radius:6px;min-width:6rem}
 .num{font-size:1.3rem;font-weight:600}
 table{border-collapse:collapse;width:100%;font-size:.85rem;margin-top:.5rem}
 td,th{padding:.25rem .5rem;border-bottom:1px solid #2a2a2a;text-align:left}
 .ok{color:#5c5} .fail{color:#e66} .muted{color:#999}
</style></head>
<body>
<h1>ramcheck — live judge monitor</h1>
<div class="bar"><div id="barfill"></div></div>
<div class="grid">
 <div class="card"><div class="muted">Fortschritt</div><div class="num"><span id="done">0</span>/<span id="total">0</span></div></div>
 <div class="card"><div class="muted">&Oslash; Score</div><div class="num" id="mean">&ndash;</div></div>
 <div class="card"><div class="muted">Red-Flags</div><div class="num fail" id="red">0</div></div>
 <div class="card"><div class="muted">ETA</div><div class="num" id="eta">&ndash;</div></div>
</div>
<h2>Score-Verteilung</h2>
<div class="grid" id="histwrap"></div>
<h2>Verdicts</h2>
<table><thead><tr><th>#</th><th>Prompt</th><th>Modell &middot; Variante</th><th>Score</th><th>Red?</th><th>Begr&uuml;ndung</th></tr></thead>
<tbody id="rows"></tbody></table>
<h2>Master-Scorecard (am Ende)</h2>
<table><thead><tr><th>Modell &middot; Variante</th><th>In %</th><th>Sicherheit</th><th>Empfehlung</th></tr></thead>
<tbody id="masterbody"></tbody></table>
<script>
function fmtEta(s){if(s==null)return '\\u2013';s=Math.round(s);return Math.floor(s/60)+'m '+(s%60)+'s';}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
const es=new EventSource('/events');
es.addEventListener('view',e=>{let v;try{v=JSON.parse(e.data)}catch(_){return}
 done.textContent=v.done; total.textContent=v.total;
 mean.textContent=v.mean_score!=null?v.mean_score.toFixed(2):'\\u2013';
 red.textContent=v.red_flags;
 eta.textContent=v.finished?'fertig':fmtEta(v.eta_s);
 barfill.style.width=(v.total?100*v.done/v.total:0)+'%';
 histwrap.innerHTML=[1,2,3,4,5].map(s=>`<div class="card"><div class="muted">Score ${s}</div><div class="num">${(v.histogram&&v.histogram[s])||0}</div></div>`).join('');
 rows.innerHTML=v.verdicts.slice().reverse().map(c=>{
  const sc=c.unscored?'<span class="muted">\\u2014</span>':c.score;
  const rf=c.red_flag?'<span class="fail">\\ud83d\\udd34</span>':'';
  return `<tr><td>${c.i}</td><td>${esc(c.prompt_id)}</td><td>${esc(c.model)} \\u00b7 ${esc(c.variant)}</td><td>${sc}</td><td>${rf}</td><td class="muted">${esc(c.rationale)}</td></tr>`;
 }).join('');
 masterbody.innerHTML=v.masters.map(m=>{
  const safe=m.safety_passed?'<span class="ok">ja</span>':`<span class="fail">nein</span> <span class="muted">(${esc(m.safety_reason)})</span>`;
  return `<tr><td>${esc(m.model)} \\u00b7 ${esc(m.variant)}</td><td>${m.pct.toFixed(1)} %</td><td>${safe}</td><td>${esc(m.recommendation)}</td></tr>`;
 }).join('');});
es.onerror=()=>{document.title='ramcheck judge monitor (offline)';};
</script></body></html>"""


def judge_start_event(ts: float, total: int) -> dict[str, object]:
    return {"ts": ts, "type": JUDGE_START, "total": total}


def verdict_event(
    ts: float,
    i: int,
    model: str,
    variant: str,
    prompt_id: str,
    repeat: int,
    category: str,
    score: int,
    red_flag: bool,
    unscored: bool,
    rationale: str,
) -> dict[str, object]:
    return {
        "ts": ts,
        "type": VERDICT,
        "i": i,
        "model": model,
        "variant": variant,
        "prompt_id": prompt_id,
        "repeat": repeat,
        "category": category,
        "score": score,
        "red_flag": red_flag,
        "unscored": unscored,
        "rationale": rationale[:RATIONALE_MAX],
    }


def master_event(
    ts: float,
    model: str,
    variant: str,
    pct: float,
    safety_passed: bool,
    safety_reason: str,
    recommendation: str,
) -> dict[str, object]:
    return {
        "ts": ts,
        "type": MASTER,
        "model": model,
        "variant": variant,
        "pct": pct,
        "safety_passed": safety_passed,
        "safety_reason": safety_reason,
        "recommendation": recommendation,
    }


def judge_done_event(ts: float, total: int, scored: int) -> dict[str, object]:
    return {"ts": ts, "type": JUDGE_DONE, "total": total, "scored": scored}


def dumps(event: dict[str, object]) -> str:
    return json.dumps(event, ensure_ascii=False)


def parse_line(line: str) -> dict[str, object] | None:
    """Parse one judge_events.jsonl line; None on empty/partial/malformed lines."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict) or "type" not in obj:
        return None
    return obj


VerdictKey = tuple[str, str, str, int]  # (model, variant, prompt_id, repeat)
ETA_GAP_FLOOR = 0.05  # ignore sub-50ms gaps (the resume replay burst) when estimating ETA


def _as_int(x: object, default: int = 0) -> int:
    return int(x) if isinstance(x, (int, float, str)) else default


def _as_float(x: object, default: float = 0.0) -> float:
    return float(x) if isinstance(x, (int, float, str)) else default


@dataclass
class VerdictView:
    key: VerdictKey
    i: int
    model: str
    variant: str
    prompt_id: str
    category: str
    score: int
    red_flag: bool
    unscored: bool
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "key": list(self.key),
            "i": self.i,
            "model": self.model,
            "variant": self.variant,
            "prompt_id": self.prompt_id,
            "category": self.category,
            "score": self.score,
            "red_flag": self.red_flag,
            "unscored": self.unscored,
            "rationale": self.rationale,
        }


@dataclass
class MasterRow:
    model: str
    variant: str
    pct: float
    safety_passed: bool
    safety_reason: str
    recommendation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "variant": self.variant,
            "pct": self.pct,
            "safety_passed": self.safety_passed,
            "safety_reason": self.safety_reason,
            "recommendation": self.recommendation,
        }


@dataclass
class JudgeRunView:
    total: int = 0
    done: int = 0
    histogram: dict[int, int] = field(default_factory=lambda: {s: 0 for s in (1, 2, 3, 4, 5)})
    red_flags: int = 0
    mean_score: float | None = None
    eta_s: float | None = None
    verdicts: list[VerdictView] = field(default_factory=list)
    masters: list[MasterRow] = field(default_factory=list)
    finished: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "done": self.done,
            "histogram": {str(k): v for k, v in self.histogram.items()},
            "red_flags": self.red_flags,
            "mean_score": self.mean_score,
            "eta_s": self.eta_s,
            "finished": self.finished,
            "verdicts": [v.as_dict() for v in self.verdicts],
            "masters": [m.as_dict() for m in self.masters],
        }


def _key(ev: dict[str, object]) -> VerdictKey:
    return (str(ev["model"]), str(ev["variant"]), str(ev["prompt_id"]), _as_int(ev["repeat"]))


def _eta(ts_list: list[float], total: int, done: int) -> float | None:
    gaps = [b - a for a, b in itertools.pairwise(ts_list) if (b - a) > ETA_GAP_FLOOR]
    if not gaps or total <= done:
        return None
    return (sum(gaps) / len(gaps)) * (total - done)


def build_view(events: Iterable[dict[str, object]]) -> JudgeRunView:
    """Fold a judge event stream into a render-ready view. Verdicts dedup by cell key
    (last wins) so a cell re-judged across crash+resume counts once."""
    total = 0
    finished = False
    by_key: dict[VerdictKey, VerdictView] = {}
    order: list[VerdictKey] = []
    masters_by: dict[tuple[str, str], MasterRow] = {}
    master_order: list[tuple[str, str]] = []
    ts_list: list[float] = []
    for e in events:
        t = e.get("type")
        if t == JUDGE_START:
            total = max(total, _as_int(e.get("total", 0)))
        elif t == JUDGE_DONE:
            finished = True
        elif t == VERDICT:
            k = _key(e)
            if k not in by_key:
                order.append(k)
            by_key[k] = VerdictView(
                key=k,
                i=_as_int(e.get("i", -1), -1),
                model=str(e["model"]),
                variant=str(e["variant"]),
                prompt_id=str(e["prompt_id"]),
                category=str(e.get("category", "")),
                score=_as_int(e.get("score", 0)),
                red_flag=bool(e.get("red_flag")),
                unscored=bool(e.get("unscored")),
                rationale=str(e.get("rationale", "")),
            )
            ts_list.append(_as_float(e.get("ts", 0.0)))
        elif t == MASTER:
            mk = (str(e["model"]), str(e["variant"]))
            if mk not in masters_by:
                master_order.append(mk)
            masters_by[mk] = MasterRow(
                model=mk[0],
                variant=mk[1],
                pct=_as_float(e.get("pct", 0.0)),
                safety_passed=bool(e.get("safety_passed")),
                safety_reason=str(e.get("safety_reason", "")),
                recommendation=str(e.get("recommendation", "")),
            )
    verdicts = [by_key[k] for k in order]
    # "scored" = a usable 1..5 score. Guards against a corrupt/out-of-range score
    # (e.g. a malformed events line) polluting the histogram/mean while done still counts it.
    scored = [v for v in verdicts if not v.unscored and 1 <= v.score <= 5]
    histogram = {s: sum(1 for v in scored if v.score == s) for s in (1, 2, 3, 4, 5)}
    mean = (sum(v.score for v in scored) / len(scored)) if scored else None
    # red_flags aggregates only scored verdicts, mirroring scorecard.red_flagged_prompts
    # (which also ignores unscored); a per-row red_flag still shows in VerdictView.red_flag.
    return JudgeRunView(
        total=total,
        done=len(verdicts),
        histogram=histogram,
        red_flags=sum(1 for v in scored if v.red_flag),
        mean_score=mean,
        eta_s=_eta(ts_list, total, len(verdicts)),
        verdicts=verdicts,
        masters=[masters_by[k] for k in master_order],
        finished=finished,
    )
