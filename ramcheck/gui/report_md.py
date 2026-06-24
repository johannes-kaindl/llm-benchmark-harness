"""Render a *complete* human-readable Markdown report for one eval bundle.

The result view shows the data interactively; this assembles EVERYTHING — every
prompt, every answer, every measurement, every dimension rationale, the scoring
method, and the full metric glossary — into a single, internally-linked Markdown
document for download/sharing. Pure: `bundle_detail` dict + glossary → str, so it
is unit-testable without a server.

Structure is inspired by the handover-note schema: a table of contents, sectioned
body, internal anchor links (prompt ↔ dimension ↔ answer), and a glossary section
at the end whose terms are the link targets for the metrics used above. Anchors are
explicit HTML `<a id="…">` so the document is portable (GitHub, Obsidian, VS Code).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
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


def _fence(text: str, lang: str = "text") -> str:
    """A fenced code block whose fence is longer than any backtick run in the content,
    so arbitrary model output containing ``` cannot break out and corrupt the document."""
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def _metric_link(key: str, glossary: Mapping[str, Glossary], label: str | None = None) -> str:
    """A label that links to its glossary anchor (when the key is defined)."""
    text = label if label is not None else (glossary[key].term if key in glossary else key)
    return f"[{text}](#glossar-{key})" if key in glossary else text


def _prompt_anchor(pid: str) -> str:
    return f"prompt-{pid}"


def _link_cited(text: str, cited: list[str]) -> str:
    """Append clickable links to cited prompt ids after a rationale."""
    if not cited:
        return text
    links = ", ".join(f"[{pid}](#{_prompt_anchor(pid)})" for pid in cited)
    return f"{text}  \n  _Belege: {links}_"


def render_report_md(detail: dict[str, Any], glossary: Mapping[str, Glossary]) -> str:
    """Assemble the full Markdown report for a (judged or eval-only) bundle."""
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

    out: list[str] = []
    w = out.append

    # ── Title + meta ────────────────────────────────────────────────────────
    models = ", ".join(sorted({r.model for r in responses})) or "—"
    machine = host.get("machine") or manifest.get("machine") or "—"
    chip = host.get("chip", "")
    ram = host.get("ram_gb", "")
    hw = f"{chip} · {ram} GB" if chip else machine
    date = manifest.get("date", "—")
    seed = manifest.get("seed", pack.sampling.seed)
    w(f"# Ergebnis-Report — {run_name}\n")
    w(
        f"> **Pack:** {pack.title} (v{pack.version}) · **Modelle:** {models} · "
        f"**Hardware:** {hw} · **Maschine-Label:** {machine} · **Datum:** {date} · **Seed:** {seed}\n"
    )
    w(
        "> _Generiert aus dem Bundle. Interne Links springen zwischen Prompts, Dimensionen "
        "und dem Metrik-Glossar; jede Kennzahl verweist auf ihre Definition._\n"
    )

    # ── Table of contents ───────────────────────────────────────────────────
    w("## Inhalt\n")
    toc = [
        ("Überblick & Urteil", "ueberblick"),
        ("Bewertungs-Methode", "methode"),
        ("Hardware & Konfiguration", "hardware"),
        ("Master-Scorecard", "scorecard"),
        ("Dimensionen", "dimensionen"),
        ("Prompt-Varianten", "varianten"),
        ("Prompts & Antworten", "prompts"),
        ("Metrik-Glossar", "glossar"),
    ]
    for label, anchor in toc:
        w(f"- [{label}](#{anchor})")
    w("")

    # ── Überblick & Urteil ──────────────────────────────────────────────────
    w('<a id="ueberblick"></a>\n## Überblick & Urteil\n')
    if master_rows:
        w(f"| Modell | Variante | {glossary['quality_pct'].term} | Urteil | Sicherheit |")
        w("|---|---|---|---|---|")
        for row in master_rows:
            safe = "✓" if row.get("safety_passed") else f"✗ K.-o. ({row.get('safety_reason', '')})"
            w(
                f"| {row['model']} | {row['variant']} | {_fmt(row.get('pct'), '{:.0f}', '%')} "
                f"| {row.get('recommendation', '—')} | {safe} |"
            )
        w("")
    else:
        w("_Noch nicht bewertet (eval-only). Tech-Specs unten sind gefüllt, Qualität offen._\n")

    # ── Bewertungs-Methode ──────────────────────────────────────────────────
    w('<a id="methode"></a>\n## Bewertungs-Methode\n')
    ko = pack.ko_rule
    w(
        "Die Master-Dimensionen werden **holistisch** bewertet — ein einziger Judge-Aufruf "
        "über *alle* Antworten eines Modells liefert pro Dimension einen Wert 1–5 plus eine "
        "Begründung, die konkrete Prompt-IDs als Beleg nennt.\n"
    )
    w("**Gewichtete Master-Scorecard:**\n")
    w("```\nΣ (Score × Gewicht) / Max × 100 = Qualität %\n```")
    w("**K.-o.-Logik** (zwei unabhängige Zweige — einer genügt für „Nein“):\n")
    w(
        f"- *Dimensions-Floor* — eine Schlüssel-Dimension liegt ≤ Schwelle "
        f"(hier: **{ko.dimension} ≤ {ko.threshold}**)."
    )
    w("- *Red-Flag-Prompt* — eine sicherheitskritische Aufgabe wurde als Red-Flag markiert.\n")
    w("**1–5-Skala:** " + " · ".join(f"{k} = {v}" for k, v in sorted(pack.scale.items())) + "\n")
    w(
        "**Reasoning-only:** Schreibt ein „Thinking“-Modell alles ins Reasoning-Feld ohne "
        "sichtbare Antwort, wird die Antwort als *reasoning-only* markiert und aus dem Mittel "
        "**ausgenommen** (Setup-Hinweis, kein Urteil). Eine wirklich leere Ausgabe bleibt 1.\n"
    )

    # ── Hardware & Konfiguration ────────────────────────────────────────────
    w('<a id="hardware"></a>\n## Hardware & Konfiguration\n')
    w(f"- **Chip:** {chip or '—'}")
    w(f"- **RAM:** {f'{ram} GB' if ram else '—'}")
    w(f"- **Maschine-Label (Config):** {machine}")
    w(f"- **Seed:** {seed}")
    w(f"- **Sampling:** temperature {pack.sampling.temperature}, seed {pack.sampling.seed}")
    w(f"- **Engine:** {host.get('engine', manifest.get('engine', '—'))}\n")

    # ── Master-Scorecard (per model × variant, with rationales) ─────────────
    w('<a id="scorecard"></a>\n## Master-Scorecard\n')
    reports_by = {(r.model, r.variant): r for r in reports}
    if reports:
        for row in master_rows:
            mv_key = (row["model"], row["variant"])
            rep = reports_by.get(mv_key)
            w(f"### {row['model']} · Variante `{row['variant']}`\n")
            w(
                f"**{_metric_link('quality_pct', glossary)}: "
                f"{_fmt(row.get('pct'), '{:.0f}', '%')}** · Urteil: **{row.get('recommendation', '—')}**"
                + ("" if row.get("safety_passed") else f" · ⛔ {row.get('safety_reason', '')}")
                + "\n"
            )
            if rep and rep.dim_scores:
                w("| Dimension | Gewicht | Score | Begründung |")
                w("|---|---|---|---|")
                for dim in pack.dimensions:
                    score = rep.dim_scores.get(dim.id)
                    rationale = (rep.dim_rationales or {}).get(dim.id, "")
                    cite_key = f"{row['model']}|{row['variant']}|{dim.id}"
                    rationale = _link_cited(rationale, cited_ids.get(cite_key, []))
                    ko_mark = (
                        " **⛔ K.-o.**"
                        if dim.id == ko.dimension and score is not None and score <= ko.threshold
                        else ""
                    )
                    w(
                        f"| [{dim.id} {dim.name}](#dim-{dim.id}) | {dim.weight} "
                        f"| {score if score is not None else '—'}{ko_mark} | {rationale or '—'} |"
                    )
                w("")
    else:
        w("_Keine Bewertung vorhanden (eval-only)._\n")

    # ── Dimensionen ─────────────────────────────────────────────────────────
    w('<a id="dimensionen"></a>\n## Dimensionen\n')
    for dim in pack.dimensions:
        w(f'<a id="dim-{dim.id}"></a>')
        w(f"### {dim.id} · {dim.name} (Gewicht {dim.weight})\n")
        if dim.about:
            w(f"{dim.about}\n")

    # ── Prompt-Varianten ────────────────────────────────────────────────────
    w('<a id="varianten"></a>\n## Prompt-Varianten\n')
    for pv in pack.prompt_variants:
        w(f"### `{pv.id}`\n")
        if pv.system_prompt:
            w(_fence(pv.system_prompt) + "\n")
        else:
            w("_(kein System-Prompt)_\n")

    # ── Prompts & Antworten ─────────────────────────────────────────────────
    w('<a id="prompts"></a>\n## Prompts & Antworten\n')
    verdict_by = {(v.model, v.variant, v.prompt_id, v.repeat): v for v in verdicts}
    for cat in pack.categories:
        w(f"### Kategorie {cat.id} · {cat.name}\n")
        for p in cat.prompts:
            w(f'<a id="{_prompt_anchor(p.id)}"></a>')
            flags = []
            if p.safety_critical:
                flags.append("🛡️ sicherheitskritisch")
            if p.format_strict:
                flags.append("📐 format-strikt")
            w(f"#### {p.id} · {p.title}" + (f"  ({' · '.join(flags)})" if flags else "") + "\n")
            w("**Prompt:**\n")
            w(_fence(p.prompt) + "\n")
            if p.tests:
                w(f"**Bewertungs-Rubrik:** {p.tests}\n")
            if p.green_flags:
                w("**Green-Flags:** " + ", ".join(f"✅ {f}" for f in p.green_flags) + "\n")
            if p.red_flags:
                w("**Red-Flags:** " + ", ".join(f"❌ {f}" for f in p.red_flags) + "\n")

            answers = [r for r in responses if r.prompt_id == p.id]
            if not answers:
                w("_Keine Antworten in diesem Lauf._\n")
            for r in answers:
                w(f"##### Antwort — {r.model} / `{r.variant}` (Wiederholung {r.repeat})\n")
                if r.content_empty:
                    note = "leer"
                    if r.reasoning_chars > 0:
                        note += f" · nur Reasoning ({r.reasoning_chars} Zeichen)"
                    w(f"_(Antwort {note})_\n")
                else:
                    w(_fence(r.response_text) + "\n")
                if r.reasoning_text:
                    w("<details><summary>💭 Reasoning anzeigen</summary>\n")
                    w(_fence(r.reasoning_text))
                    w("</details>\n")
                # Per-answer measurements
                total_tp = (
                    (r.prompt_tokens + r.completion_tokens) / r.e2e_s
                    if _is_num(r.e2e_s) and r.e2e_s > 0
                    else math.nan
                )
                w("| Kennzahl | Wert |")
                w("|---|---|")
                w(f"| {_metric_link('ttft_p50', glossary, 'TTFT')} | {_fmt(r.ttft_s, '{:.2f}', 's')} |")
                w(f"| {_metric_link('decode_median', glossary, 'Decode')} | {_fmt(r.decode_tps, '{:.0f}', 'tok/s')} |")
                w(f"| {_metric_link('prefill_tps', glossary, 'Prefill')} | {_fmt(r.prefill_tps, '{:.0f}', 'tok/s')} |")
                w(f"| {_metric_link('e2e', glossary, 'Gesamtzeit')} | {_fmt(r.e2e_s, '{:.2f}', 's')} |")
                w(f"| {_metric_link('total_throughput', glossary, 'Gesamt-Durchsatz')} | {_fmt(total_tp, '{:.0f}', 'tok/s')} |")
                w(f"| Tokens (Prompt→Antwort) | {r.prompt_tokens} → {r.completion_tokens} |")
                w(f"| {_metric_link('system_peak_ram', glossary, 'System-Peak')} | {_fmt(getattr(r, 'sys_used_mb', None) and r.sys_used_mb / 1024, '{:.1f}', 'GB')} |")
                w(f"| {_metric_link('model_delta_ram', glossary, 'Modell-Delta')} | {_fmt(getattr(r, 'sys_used_delta_mb', None) and r.sys_used_delta_mb / 1024, '{:.1f}', 'GB')} |")
                if _is_num(getattr(r, "reasoning_duration_s", math.nan)) and r.reasoning_duration_s > 0:
                    w(f"| {_metric_link('reasoning_duration', glossary, 'Thinking-Dauer')} | {_fmt(r.reasoning_duration_s, '{:.2f}', 's')} |")
                    w(f"| {_metric_link('reasoning_tps', glossary, 'Thinking-Tempo')} | {_fmt(r.reasoning_tps, '{:.0f}', 'tok/s')} |")
                w("")
                v = verdict_by.get((r.model, r.variant, p.id, r.repeat))
                if v is not None:
                    badge = f"**{v.score}/5**"
                    if v.unscored:
                        badge += " · _unscored (reasoning-only)_"
                    if v.red_flag:
                        badge += " · ❌ **Red-Flag**"
                    w(f"**Judge:** {badge}" + (f" — {v.rationale}" if v.rationale else "") + "\n")

    # ── Metrik-Glossar ──────────────────────────────────────────────────────
    w('<a id="glossar"></a>\n## Metrik-Glossar\n')
    w("_Jede Kennzahl oben verlinkt hierher._\n")
    for key, g in glossary.items():
        w(f'<a id="glossar-{key}"></a>')
        w(f"### {g.term}\n")
        w(f"{g.short}\n")
        if g.long:
            w(f"{g.long}\n")

    return "\n".join(out) + "\n"
