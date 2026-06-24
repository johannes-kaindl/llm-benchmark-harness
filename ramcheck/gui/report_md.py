"""Render a *complete* Obsidian-native Markdown report for one eval bundle.

The result view shows the data interactively; this assembles EVERYTHING — every
prompt, every answer, every measurement, every dimension rationale, the scoring
method, and the full metric glossary — into one downloadable note. Pure:
`bundle_detail` dict + glossary → str, so it is unit-testable without a server.

Obsidian-native by design:
- **YAML frontmatter** encodes every comparable setup fact + headline result
  (`type: testrun`, numeric perf/quality, lists) so many testruns can be compared
  in a Base (`/obsidian-bases`).
- **Internal links are wikilinks** `[[#Heading|display]]` (Obsidian heading links).
- **Long free text** (prompts, answers, reasoning, system-prompts) is wrapped in
  collapsed callouts `> [!kind]- title` so the note stays scannable (ADHS-friendly);
  every content line is `> `-prefixed so arbitrary model output stays contained.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from statistics import median
from typing import Any

from ramcheck.gui.glossary import Glossary


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))


def _fmt(v: Any, fmt: str = "{:.2f}", unit: str = "") -> str:
    """Format a numeric value, or '—' when it is None/NaN."""
    if not _is_num(v):
        return "—"
    s = fmt.format(v)
    return f"{s} {unit}".strip() if unit else s


def _to_num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[-+]?\d*\.?\d+", str(v or ""))
    return float(m.group()) if m else None


def _round(v: float | None, ndigits: int) -> float | int | None:
    if v is None:
        return None
    r = round(v, ndigits)
    return int(r) if float(r).is_integer() else r


def _yaml(v: Any) -> str:
    """A YAML scalar: numbers bare (Bases-sortable), bools lower, None null, strings quoted."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return json.dumps(str(v), ensure_ascii=False)


def _cell(text: str) -> str:
    """Make arbitrary prose safe inside a Markdown table cell (escape pipes, fold newlines)."""
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", "<br>").strip()


def _wl(heading: str, display: str | None = None) -> str:
    """An Obsidian same-note heading wikilink `[[#Heading|display]]`."""
    return f"[[#{heading}|{display}]]" if display is not None else f"[[#{heading}]]"


def _callout(kind: str, title: str, body: str) -> str:
    """A collapsed Obsidian callout; every body line is `> `-prefixed so arbitrary
    multi-line model output (blank lines, code fences, headings) stays contained."""
    lines = body.split("\n")
    quoted = "\n".join(f"> {ln}" if ln.strip() else ">" for ln in lines)
    return f"> [!{kind}]- {title}\n{quoted}"


def _metric_link(key: str, glossary: Mapping[str, Glossary], label: str | None = None) -> str:
    """A label that wikilinks to its glossary heading (the term), when defined."""
    if key in glossary:
        term = glossary[key].term
        return _wl(term, label if label is not None else term)
    return label if label is not None else key


def _frontmatter(
    *,
    run_name: str,
    pack: Any,
    manifest: dict[str, Any],
    host: dict[str, Any],
    responses: list[Any],
    master_rows: list[dict[str, Any]],
    perf: dict[str, Any],
    known_ids: set[str],
) -> list[str]:
    models = sorted({r.model for r in responses})
    variants = sorted({r.variant for r in responses})
    ok_resp = [r for r in responses if r.ok and not getattr(r, "is_cold_start", False)]
    engine = host.get("engine") or manifest.get("engine") or (responses[0].engine if responses else None)
    engine_version = (
        host.get("engine_version")
        or manifest.get("engine_version")
        or (responses[0].engine_version if responses else None)
    )

    # headline result = best-scoring (model, variant); per-variant quality kept too
    scored = [r for r in master_rows if r.get("pct") is not None]
    best = max(scored, key=lambda r: r["pct"]) if scored else None
    q_by_variant: dict[str, int] = {}
    for r in scored:
        q_by_variant[r["variant"]] = max(q_by_variant.get(r["variant"], 0), round(r["pct"]))

    deltas = [
        r.sys_used_delta_mb for r in ok_resp if getattr(r, "sys_used_delta_mb", None) is not None
    ]
    rdurs = [
        r.reasoning_duration_s
        for r in ok_resp
        if _is_num(getattr(r, "reasoning_duration_s", math.nan)) and r.reasoning_duration_s > 0
    ]

    fm: list[tuple[str, Any]] = [
        ("title", run_name),
        ("type", "testrun"),
        ("date", manifest.get("date")),
        ("pack", pack.id),
        ("pack_title", pack.title),
        ("pack_version", pack.version),
        ("machine", host.get("machine") or manifest.get("machine")),
        ("cpu", host.get("chip") or None),
        ("gb_ram", _round(_to_num(host.get("ram_gb")), 1)),
        ("engine", engine or None),
        ("engine_version", engine_version or None),
        ("model", models[0] if len(models) == 1 else None),  # scalar for single-model runs (Bases groupBy)
        ("seed", manifest.get("seed", pack.sampling.seed)),
        ("temperature", pack.sampling.temperature),
        ("n_prompts", len(known_ids)),
        ("n_answers", len(responses)),
        ("recommendation", best["recommendation"] if best else None),
        ("quality_pct", round(best["pct"]) if best else None),
        ("safety_passed", best["safety_passed"] if best else None),
        ("ttft_p50_s", _round(_to_num(perf.get("ttft_p50")), 2)),
        ("decode_med_tps", _round(_to_num(perf.get("decode_med")), 1)),
        ("e2e_med_s", _round(_to_num(perf.get("e2e_med")), 2)),
        ("peak_ram_gb", _round(_to_num(perf.get("peak_ram_gb")), 1)),
        ("model_delta_gb", _round(max(deltas) / 1024, 1) if deltas else None),
        ("reasoning_duration_s", _round(median(rdurs), 2) if rdurs else None),
    ]

    out = ["---"]
    for k, v in fm:
        out.append(f"{k}: {_yaml(v)}")
    for key, vals in (("models", models), ("variants", variants)):
        out.append(f"{key}:")
        out.extend(f"  - {_yaml(x)}" for x in vals)
    if q_by_variant:
        for v, q in sorted(q_by_variant.items()):
            out.append(f"q_{re.sub(r'[^a-zA-Z0-9_]+', '_', v)}: {q}")
    out.append("---")
    return out


def _eval_task(pack: Any, responses: list[Any], prompt_link: Any, known_ids: set[str]) -> str:
    """The 'Bewertungs-Auftrag': instructions + a fillable scorecard per (model, variant),
    so an unjudged report can be handed to a cloud AI (or a person) to evaluate."""
    ko = pack.ko_rule
    rf = ", ".join(prompt_link(pid) for pid in ko.red_flag_prompts if pid in known_ids) or "—"
    b: list[str] = ["## 📋 Bewertungs-Auftrag\n"]
    b.append(
        "Dieser Report enthält die Antworten, aber **noch keine qualitative Bewertung**. "
        "Aufgabe für die bewertende KI (oder Person):\n"
    )
    b.append(
        f"1. Lies den Einsatzzweck (oben), die {_wl('Bewertungs-Methode')}, die "
        f"{_wl('Dimensionen')} (inkl. 1–5-Skala) und die {_wl('Prompts & Antworten')}."
    )
    b.append(
        "2. Bewerte **holistisch** pro Dimension über *alle* Antworten eines Modells — ein "
        "Score **1–5** plus eine Begründung, die konkrete **Prompt-IDs** zitiert."
    )
    b.append(
        f"3. Prüfe die **K.-o.-Regeln**: Dimensions-Floor (**{ko.dimension} ≤ {ko.threshold}**) "
        f"und Red-Flag-Prompts ({rf}). Ein Treffer in einem Zweig genügt für „Nein“."
    )
    b.append(
        "4. Berechne die gewichtete **Qualität %** = Σ(Score × Gewicht) / (5 × ΣGewicht) × 100 "
        "und gib ein **Gesamturteil** (Ja / Mit Einschränkung / Nein)."
    )
    b.append("5. Trage deine Bewertung in die Vorlage(n) unten ein.\n")
    for model, variant in sorted({(r.model, r.variant) for r in responses}):
        b.append(f"### Vorlage: {model} · Variante `{variant}`\n")
        b.append("| Dimension | Gewicht | Score (1–5) | Begründung (mit Prompt-IDs) |")
        b.append("|---|---|---|---|")
        for dim in pack.dimensions:
            b.append(f"| {dim.id} · {dim.name} | {dim.weight} |  |  |")
        b.append("")
        b.append("- **Gewichtete Qualität %:** ")
        b.append(f"- **K.-o.-Prüfung:** {ko.dimension} ≤ {ko.threshold}? ___ · Red-Flag bei {rf}? ___")
        b.append("- **Gesamturteil:** Ja / Mit Einschränkung / Nein — ")
        b.append("")
    return "\n".join(b)


def render_report_md(
    detail: dict[str, Any], glossary: Mapping[str, Glossary], *, include_judging: bool = True
) -> str:
    """Assemble the full Obsidian Markdown report for a bundle.

    `include_judging=False` strips every qualitative judgement (scorecard, dimension
    rationales, per-answer verdicts) and instead embeds a **Bewertungs-Auftrag** — an
    instruction + fillable scorecard template — so the unjudged report can be handed to
    a cloud AI (or a person) to evaluate.
    """
    run_dir = detail.get("run_dir")
    run_name = run_dir.name if run_dir is not None else "bundle"
    pack = detail["pack"]
    manifest: dict[str, Any] = detail.get("manifest") or {}
    host: dict[str, Any] = manifest.get("host") or {}
    responses: list[Any] = detail.get("responses") or []
    verdicts: list[Any] = detail.get("verdicts") or []
    reports: list[Any] = detail.get("reports") or []
    master_rows: list[dict[str, Any]] = detail.get("master_rows") or []
    cited_ids: dict[str, list[str]] = detail.get("cited_ids") or {}
    perf: dict[str, Any] = detail.get("perf") or {}
    known_ids = {p.id for _, p in pack.all_prompts()}
    title_by = {p.id: p.title for _, p in pack.all_prompts()}
    if not include_judging:
        # strip every qualitative judgement so a cloud AI can produce a fresh one
        master_rows, reports, verdicts = [], [], []

    def prompt_link(pid: str, display: str | None = None) -> str:
        return _wl(f"{pid} · {title_by[pid]}", display or pid) if pid in title_by else pid

    out: list[str] = []
    w = out.append

    # ── Frontmatter ─────────────────────────────────────────────────────────
    out.extend(
        _frontmatter(
            run_name=run_name,
            pack=pack,
            manifest=manifest,
            host=host,
            responses=responses,
            master_rows=master_rows,
            perf=perf,
            known_ids=known_ids,
        )
    )

    # ── Title + meta ────────────────────────────────────────────────────────
    models = ", ".join(sorted({r.model for r in responses})) or "—"
    machine = host.get("machine") or manifest.get("machine") or "—"
    chip = host.get("chip", "")
    ram = _to_num(host.get("ram_gb"))
    hw = f"{chip} · {ram:.0f} GB" if chip and ram else machine
    w(f"\n# Ergebnis-Report — {run_name}\n")
    w(
        f"> **Pack:** {pack.title} (v{pack.version}) · **Modelle:** {models} · "
        f"**Hardware:** {hw} · **Maschine-Label:** {machine} · **Datum:** {manifest.get('date', '—')}\n"
    )
    if pack.description:
        w(f"{pack.description}\n")

    # ── Table of contents ───────────────────────────────────────────────────
    w("## Inhalt\n")
    toc_items = ["Überblick & Urteil"]
    if not include_judging:
        toc_items.append("📋 Bewertungs-Auftrag")
    toc_items += ["Bewertungs-Methode", "Hardware & Konfiguration"]
    if include_judging:
        toc_items.append("Master-Scorecard")
    toc_items += ["Dimensionen", "Prompt-Varianten", "Prompts & Antworten", "Metrik-Glossar"]
    for label in toc_items:
        w(f"- {_wl(label)}")
    w("")
    top = f"\n{_wl('Inhalt', '↑ zum Inhalt')}\n"

    # ── Überblick & Urteil ──────────────────────────────────────────────────
    w("## Überblick & Urteil\n")
    if master_rows:
        w(f"| Modell | Variante | {glossary['quality_pct'].term} | Urteil | Sicherheit |")
        w("|---|---|---|---|---|")
        for row in master_rows:
            safe = "✓" if row.get("safety_passed") else f"✗ K.-o. ({row.get('safety_reason', '')})"
            w(
                f"| {_cell(str(row['model']))} | {_cell(str(row['variant']))} "
                f"| {_fmt(row.get('pct'), '{:.0f}', '%')} "
                f"| {_cell(str(row.get('recommendation', '—')))} | {_cell(safe)} |"
            )
        w("")
    else:
        w("_Noch nicht bewertet. Tech-Specs sind gefüllt, Qualität offen._\n")
    w(top)

    # ── Bewertungs-Auftrag (unjudged export — for a cloud AI / person) ──────
    if not include_judging:
        w(_eval_task(pack, responses, prompt_link, known_ids))
        w(top)

    # ── Bewertungs-Methode ──────────────────────────────────────────────────
    w("## Bewertungs-Methode\n")
    ko = pack.ko_rule
    w(
        "Die Master-Dimensionen werden **holistisch** bewertet — ein einziger Judge-Aufruf "
        "über *alle* Antworten eines Modells liefert pro Dimension einen Wert 1–5 plus eine "
        "Begründung, die konkrete Prompt-IDs als Beleg nennt.\n"
    )
    w("**Gewichtete Master-Scorecard:** `Σ (Score × Gewicht) / Max × 100 = Qualität %`\n")
    w("**K.-o.-Logik** (zwei unabhängige Zweige — einer genügt für „Nein“):\n")
    w(f"- *Dimensions-Floor* — eine Schlüssel-Dimension liegt ≤ Schwelle (hier: **{ko.dimension} ≤ {ko.threshold}**).")
    w("- *Red-Flag-Prompt* — eine sicherheitskritische Aufgabe wurde als Red-Flag markiert.\n")
    if ko.red_flag_prompts:
        links = ", ".join(prompt_link(pid) for pid in ko.red_flag_prompts if pid in known_ids)
        if links:
            w(f"**Red-Flag-Kandidaten:** {links}\n")
    w("**1–5-Skala:** " + " · ".join(f"{k} = {v}" for k, v in sorted(pack.scale.items())) + "\n")
    w(
        "**Reasoning-only:** Schreibt ein „Thinking“-Modell alles ins Reasoning-Feld ohne "
        "sichtbare Antwort, wird die Antwort als *reasoning-only* markiert und aus dem Mittel "
        "**ausgenommen** (Setup-Hinweis, kein Urteil). Eine wirklich leere Ausgabe bleibt 1.\n"
    )
    w(top)

    # ── Hardware & Konfiguration ────────────────────────────────────────────
    w("## Hardware & Konfiguration\n")
    engine = host.get("engine") or manifest.get("engine") or (responses[0].engine if responses else "")
    engine_version = (
        host.get("engine_version")
        or manifest.get("engine_version")
        or (responses[0].engine_version if responses else "")
    )
    w(f"- **Chip:** {chip or '—'}")
    w(f"- **RAM:** {f'{ram:.1f} GB' if ram else '—'}")
    w(f"- **Maschine-Label (Config):** {machine}")
    w(f"- **Seed:** {manifest.get('seed', pack.sampling.seed)}")
    w(f"- **Sampling:** temperature {pack.sampling.temperature}, seed {pack.sampling.seed}")
    w(f"- **Engine:** {engine or '—'}" + (f" ({engine_version})" if engine_version else "") + "\n")
    w(top)

    # ── Master-Scorecard (judged runs only) ─────────────────────────────────
    if include_judging:
        w("## Master-Scorecard\n")
        reports_by = {(r.model, r.variant): r for r in reports}
        if reports:
            for row in master_rows:
                rep = reports_by.get((row["model"], row["variant"]))
                w(f"### {row['model']} · Variante `{row['variant']}`\n")
                w(
                    f"**{_metric_link('quality_pct', glossary)}: "
                    f"{_fmt(row.get('pct'), '{:.0f}', '%')}** · Urteil: **{row.get('recommendation', '—')}**"
                    + ("" if row.get("safety_passed") else f" · ⛔ {_cell(str(row.get('safety_reason', '')))}")
                    + "\n"
                )
                if rep and rep.dim_scores:
                    w("| Dimension | Gewicht | Score | Begründung |")
                    w("|---|---|---|---|")
                    for dim in pack.dimensions:
                        score = rep.dim_scores.get(dim.id)
                        rationale = (rep.dim_rationales or {}).get(dim.id, "")
                        cite_key = f"{row['model']}|{row['variant']}|{dim.id}"
                        cited = [c for c in cited_ids.get(cite_key, []) if c in known_ids]
                        if cited:
                            rationale = f"{rationale} · Belege: " + ", ".join(prompt_link(c) for c in cited)
                        ko_mark = (
                            " **⛔ K.-o.**"
                            if dim.id == ko.dimension and score is not None and score <= ko.threshold
                            else ""
                        )
                        w(
                            f"| {_wl(f'{dim.id} · {dim.name}', dim.id)} | {dim.weight} "
                            f"| {score if score is not None else '—'}{ko_mark} | {_cell(rationale) or '—'} |"
                        )
                    w("")
        else:
            w("_Keine Bewertung vorhanden (eval-only)._\n")
        w(top)

    # ── Dimensionen ─────────────────────────────────────────────────────────
    w("## Dimensionen\n")
    for dim in pack.dimensions:
        w(f"### {dim.id} · {dim.name}\n")
        w(f"_Gewicht: {dim.weight}_\n")
        if dim.about:
            w(f"{dim.about}\n")
    w(top)

    # ── Prompt-Varianten ────────────────────────────────────────────────────
    w("## Prompt-Varianten\n")
    for pv in pack.prompt_variants:
        gloss_key = f"variant_{pv.id}"
        label = _metric_link(gloss_key, glossary, pv.id) if gloss_key in glossary else pv.id
        w(f"### Variante: {pv.id}\n")
        w(f"{label}\n" if gloss_key in glossary else "")
        if pv.system_prompt:
            w(_callout("quote", "System-Prompt anzeigen", pv.system_prompt) + "\n")
        else:
            w("_(kein System-Prompt)_\n")
    w(top)

    # ── Prompts & Antworten ─────────────────────────────────────────────────
    w("## Prompts & Antworten\n")
    verdict_by = {(v.model, v.variant, v.prompt_id, v.repeat): v for v in verdicts}
    for cat in pack.categories:
        w(f"### Kategorie {cat.id} · {cat.name}\n")
        for p in cat.prompts:
            flags = []
            if p.safety_critical:
                flags.append("🛡️ sicherheitskritisch")
            if p.format_strict:
                flags.append("📐 format-strikt")
            w(f"#### {p.id} · {p.title}" + (f"  ({' · '.join(flags)})" if flags else "") + "\n")
            limit = "unbegrenzt" if p.max_tokens is None else str(p.max_tokens)
            w(f"_Token-Limit: {limit} · Wiederholungen: {p.repeats}_\n")
            if p.tests:
                w(f"**Rubrik:** {p.tests}\n")
            if p.green_flags:
                w("**Green-Flags:** " + ", ".join(f"✅ {f}" for f in p.green_flags) + "\n")
            if p.red_flags:
                w("**Red-Flags:** " + ", ".join(f"❌ {f}" for f in p.red_flags) + "\n")
            w(_callout("question", "Prompt anzeigen", p.prompt) + "\n")

            answers = [r for r in responses if r.prompt_id == p.id]
            if not answers:
                w("_Keine Antworten in diesem Lauf._\n")
            for r in answers:
                v = verdict_by.get((r.model, r.variant, p.id, r.repeat))
                cold = " · ❄️ Cold-Start" if getattr(r, "is_cold_start", False) else ""
                judge = f" · Judge {v.score}/5" if v is not None else ""
                title = f"Antwort · {r.model} / {r.variant} · Wdh {r.repeat}{judge}{cold}"
                w(_callout("quote", title, _answer_body(r, v, glossary)) + "\n")
            w(top)

    # ── Metrik-Glossar ──────────────────────────────────────────────────────
    w("## Metrik-Glossar\n")
    w("_Die Kennzahlen oben verlinken hierher._\n")
    for _key, g in glossary.items():
        w(f"### {g.term}\n")
        w(f"{g.short}\n")
        if g.long:
            w(f"{g.long}\n")
    w(top)

    return "\n".join(out) + "\n"


def _answer_body(r: Any, v: Any, glossary: Mapping[str, Glossary]) -> str:
    """The full per-answer content that goes (collapsed) inside one callout."""
    b: list[str] = []
    if not r.ok:
        b.append(f"⚠️ **Anfrage fehlgeschlagen:** {r.error or 'unbekannt'}")
        return "\n".join(b)
    if r.content_empty:
        note = "leer"
        if r.reasoning_chars > 0:
            note += f" · nur Reasoning ({r.reasoning_chars} Zeichen)"
        b.append(f"_(Antwort {note})_")
    else:
        b.append(r.response_text)
    if r.reasoning_text:
        b.append("\n**💭 Reasoning:**\n")
        b.append(r.reasoning_text)

    total_tp = (
        (r.prompt_tokens + r.completion_tokens) / r.e2e_s if _is_num(r.e2e_s) and r.e2e_s > 0 else math.nan
    )
    peak_gb = r.sys_used_mb / 1024 if getattr(r, "sys_used_mb", None) is not None else None
    delta_gb = r.sys_used_delta_mb / 1024 if getattr(r, "sys_used_delta_mb", None) is not None else None
    b.append("\n**Messwerte:**\n")
    b.append("| Kennzahl | Wert |")
    b.append("|---|---|")
    b.append(f"| {_metric_link('ttft_p50', glossary, 'TTFT')} | {_fmt(r.ttft_s, '{:.2f}', 's')} |")
    b.append(f"| {_metric_link('decode_median', glossary, 'Decode')} | {_fmt(r.decode_tps, '{:.0f}', 'tok/s')} |")
    b.append(f"| {_metric_link('prefill_tps', glossary, 'Prefill')} | {_fmt(r.prefill_tps, '{:.0f}', 'tok/s')} |")
    b.append(f"| {_metric_link('e2e', glossary, 'Gesamtzeit')} | {_fmt(r.e2e_s, '{:.2f}', 's')} |")
    b.append(f"| {_metric_link('total_throughput', glossary, 'Gesamt-Durchsatz')} | {_fmt(total_tp, '{:.0f}', 'tok/s')} |")
    b.append(f"| Tokens (Prompt→Antwort) | {r.prompt_tokens} → {r.completion_tokens} |")
    b.append(f"| {_metric_link('system_peak_ram', glossary, 'System-Peak')} | {_fmt(peak_gb, '{:.1f}', 'GB')} |")
    b.append(f"| {_metric_link('model_delta_ram', glossary, 'Modell-Delta')} | {_fmt(delta_gb, '{:.1f}', 'GB')} |")
    b.append(f"| {_metric_link('mem_pressure', glossary, 'Memory-Pressure')} | {getattr(r, 'mem_pressure_max', '') or '—'} |")
    if _is_num(getattr(r, "reasoning_duration_s", math.nan)) and r.reasoning_duration_s > 0:
        b.append(f"| {_metric_link('reasoning_duration', glossary, 'Thinking-Dauer')} | {_fmt(r.reasoning_duration_s, '{:.2f}', 's')} |")
        b.append(f"| {_metric_link('reasoning_tps', glossary, 'Thinking-Tempo')} | {_fmt(r.reasoning_tps, '{:.0f}', 'tok/s')} |")
    if getattr(r, "throttled", False):
        b.append("| Throttled | ⚠️ ja (aus Aggregaten ausgeschlossen) |")
    if getattr(r, "power_source", "") == "battery":
        b.append("| Stromquelle | 🔋 Akku (aus Aggregaten ausgeschlossen) |")
    if v is not None:
        badge = f"{v.score}/5"
        if v.unscored:
            badge += " · unscored (reasoning-only)"
        if v.red_flag:
            badge += " · ❌ Red-Flag"
        b.append(f"\n**Judge:** {badge}" + (f" — {v.rationale}" if v.rationale else ""))
    return "\n".join(b)
