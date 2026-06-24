# tests/test_gui_report_md.py
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.gui.glossary import GLOSSARY
from touchstone.gui.report_md import render_report_md
from touchstone.pack import load_pack

PACK = "packs/ndassist.yaml"
HOST = {"chip": "Apple M5 Pro", "ram_gb": "64.0 GB", "machine": "M5-64GB", "engine": "lm-studio"}


class _FakeLauncher:
    def spawn(self, argv):
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _resp_payload(prompt_id, **over):
    base = dict(
        pack_id="ndassist",
        pack_version=1,
        machine="t",
        model="m",
        quant="q",
        engine="lm-studio",
        engine_version="0",
        variant="baseline",
        category="A",
        prompt_id=prompt_id,
        repeat=0,
        response_text="Eine vollständige Antwort.",
        content_empty=False,
        ttft_s=0.2,
        decode_tps=30.0,
        prefill_tps=90.0,
        e2e_s=1.5,
        prompt_tokens=100,
        completion_tokens=50,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=0.0,
        sys_used_mb=20000.0,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.5,
        reasoning_chars=0,
    )
    base.update(over)
    return base


def _resp(prompt_id, **over):
    from touchstone.results import EvalResponse

    return EvalResponse(**_resp_payload(prompt_id, **over))


def _write_bundle(d, *, model="m", variant="baseline"):
    """A self-contained judged bundle over the real in-repo pack (one answer per prompt)."""
    pk = load_pack(PACK)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "models": [{"id": model, "quant": "q"}],
                "host": HOST,
                "date": "2026-06-24",
                "seed": 42,
            }
        ),
        encoding="utf-8",
    )
    first = next(p for _, p in pk.all_prompts())
    (d / "responses.jsonl").write_text(
        json.dumps(_resp_payload(first.id, model=model, variant=variant)) + "\n", encoding="utf-8"
    )
    header = ["model", "variant", "metric_type", "metric", "weight", "score"]
    lines = [",".join(header)]
    for dim in pk.dimensions:
        lines.append(f"{model},{variant},dimension,{dim.id},{dim.weight},4")
    (d / "scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pk, first


def _detail(pk, responses, **over):
    base = {
        "run_dir": Path("runs/x"),
        "manifest": {"host": HOST, "date": "2026-06-24"},
        "pack": pk,
        "responses": responses,
        "verdicts": [],
        "reports": [],
        "master_rows": [],
        "cited_ids": {},
        "perf": {},
    }
    base.update(over)
    return base


def test_export_report_route_serves_obsidian_markdown(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _pk, first = _write_bundle(d)
    r = _client(tmp_path).get(f"/export-report/{d.name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert f"{d.name}.md" in r.headers.get("content-disposition", "")
    body = r.text
    # YAML frontmatter for Bases
    assert body.startswith("---\n")
    assert 'type: "testrun"' in body
    assert "models:" in body and "  - " in body
    # Obsidian wikilink TOC + a metric wikilink into the glossary; inside the measurement
    # table the alias '|' must be escaped as '\\|' so the table does not break.
    assert "- [[#Bewertungs-Methode]]" in body
    assert "[[#TTFT P50\\|TTFT]]" in body
    assert "[[#TTFT P50|TTFT]]" not in body  # the unescaped (table-breaking) form must NOT appear
    # collapsed callouts for the long texts
    assert "> [!question]- Prompt anzeigen" in body
    assert "> [!quote]- Antwort ·" in body
    # the full prompt text appears (inside the callout, line-prefixed)
    assert first.prompt.split("\n")[0][:50] in body


def test_export_report_missing_bundle_is_404(tmp_path):
    r = _client(tmp_path).get("/export-report/does-not-exist")
    assert r.status_code == 404


def test_frontmatter_encodes_comparable_facts(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _pk, _first = _write_bundle(d)
    body = _client(tmp_path).get(f"/export-report/{d.name}").text
    fm = body.split("---\n")[1]
    assert 'type: "testrun"' in fm
    assert "gb_ram: 64" in fm  # parsed numeric from "64.0 GB"
    assert "quality_pct: " in fm  # judged → a headline quality number
    assert "q_baseline:" in fm  # per-variant quality is Bases-queryable
    assert "n_prompts:" in fm and "n_answers:" in fm


def test_render_report_md_surfaces_reasoning_and_ram():
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp = _resp(
        first.id,
        sys_used_mb=20000.0,
        sys_used_baseline_mb=8000.0,
        sys_used_delta_mb=12000.0,
        reasoning_chars=120,
        reasoning_text="kurzes Nachdenken",
        reasoning_duration_s=2.5,
        reasoning_tps=40.0,
        reasoning_completion_tokens=80,
    )
    md = render_report_md(_detail(pk, [resp]), GLOSSARY)
    assert "Thinking-Dauer" in md
    assert "2.50 s" in md
    assert "11.7 GB" in md  # 12000 / 1024 model delta
    assert "reasoning_duration_s: 2.5" in md  # also surfaced in frontmatter


def test_render_report_md_failed_request_shows_error_not_fake_answer():
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp = _resp(first.id, ok=False, error="ConnectionError: boom", response_text="", content_empty=True)
    md = render_report_md(_detail(pk, [resp]), GLOSSARY)
    assert "Anfrage fehlgeschlagen:** ConnectionError: boom" in md
    assert "**Messwerte:**" not in md  # no perf table for a failed answer


def test_render_report_md_adversarial_answer_and_rationale_stay_contained():
    # an answer + judge rationale containing ``` / | / headings must stay inside the
    # collapsed callout and not corrupt the document (later sections must survive).
    from touchstone.results import Verdict

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp = _resp(first.id, response_text="Code:\n```python\nprint('x')\n```\n## Antwort-Heading\nfertig.")
    verdict = Verdict(
        model="m",
        variant="baseline",
        prompt_id=first.id,
        repeat=0,
        category="A",
        score=4,
        red_flag=False,
        rationale="hat ``` und | und mehr",
        unscored=False,
        safety_critical=False,
    )
    md = render_report_md(_detail(pk, [resp], verdicts=[verdict]), GLOSSARY)
    # the answer content is callout-quoted (every line prefixed) → no bare code fence / heading
    assert "> ```python" in md
    assert "> ## Antwort-Heading" in md
    assert "\n## Antwort-Heading" not in md  # never a real top-level heading
    # the document still reaches its trailing sections (not swallowed by a stray fence)
    assert "## Metrik-Glossar" in md
    assert "**Judge:** 4/5 — hat ``` und | und mehr" in md


def test_scorecard_table_escapes_breaking_rationale():
    # the VISIBLE scorecard table holds judge rationales; a '|'/newline must be escaped.
    from touchstone.results import ModelReport

    pk = load_pack(PACK)
    dim0 = pk.dimensions[0]
    report = ModelReport(
        model="m",
        variant="baseline",
        dim_scores={dd.id: 4 for dd in pk.dimensions},
        dim_rationales={dim0.id: "gut | aber\nzeile"},
    )
    detail = _detail(
        pk,
        [],
        reports=[report],
        master_rows=[
            {
                "model": "m",
                "variant": "baseline",
                "pct": 80.0,
                "safety_passed": True,
                "safety_reason": "",
                "rubric_level": "hoch",
            }
        ],
    )
    md = render_report_md(detail, GLOSSARY)
    assert "gut \\| aber<br>zeile" in md


def test_unjudged_export_strips_judging_and_adds_eval_task():
    from touchstone.results import ModelReport, Verdict

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp = _resp(first.id)
    report = ModelReport(
        model="m", variant="baseline", dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={}
    )
    verdict = Verdict(
        model="m", variant="baseline", prompt_id=first.id, repeat=0, category="A",
        score=4, red_flag=False, rationale="gut", unscored=False, safety_critical=False,
    )
    detail = _detail(
        pk, [resp], reports=[report], verdicts=[verdict],
        master_rows=[{"model": "m", "variant": "baseline", "pct": 80.0,
                      "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
    )
    judged = render_report_md(detail, GLOSSARY, include_judging=True)
    blank = render_report_md(detail, GLOSSARY, include_judging=False)
    # judged keeps the scorecard + verdicts
    assert "## Master-Scorecard" in judged and "**Judge:**" in judged
    assert "quality_pct: 80" in judged
    # blank strips every judgement and embeds the fillable evaluation task instead
    assert "## Master-Scorecard" not in blank
    assert "**Judge:**" not in blank
    assert "## 📋 Bewertungs-Auftrag" in blank
    assert "### Vorlage: m · Variante `baseline`" in blank
    assert "| Score (1–5) | Begründung (mit Prompt-IDs) |" in blank
    assert "quality_pct: null" in blank  # no headline quality without judging


def test_export_report_blank_route_serves_evaluation_task(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _write_bundle(d)
    r = _client(tmp_path).get(f"/export-report/{d.name}?judging=0")
    assert r.status_code == 200
    assert "zum-bewerten.md" in r.headers.get("content-disposition", "")
    assert "## 📋 Bewertungs-Auftrag" in r.text
    assert "## Master-Scorecard" not in r.text


def test_judge_model_and_quant_surface_when_recorded():
    from touchstone.results import ModelReport

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp = _resp(first.id, quant="q4")
    report = ModelReport(
        model="m", variant="baseline", dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={}
    )
    detail = _detail(
        pk, [resp], reports=[report],
        master_rows=[{"model": "m", "variant": "baseline", "pct": 80.0,
                      "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
        manifest={"host": HOST, "date": "2026-06-24",
                  "judge": {"model": "qwen3.6-35b-a3b", "temperature": 0.0,
                            "endpoint": "http://localhost:1234/v1"}},
    )
    md = render_report_md(detail, GLOSSARY)
    assert "**Judge-Modell:** `qwen3.6-35b-a3b`" in md  # visible in the method section
    assert 'judge_model: "qwen3.6-35b-a3b"' in md  # frontmatter (Bases-queryable)
    assert 'quant: "q4"' in md  # frontmatter
    assert "**Modell-Quant:** q4" in md  # visible


def test_judge_model_unknown_for_old_judged_bundle():
    from touchstone.results import ModelReport

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    report = ModelReport(
        model="m", variant="baseline", dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={}
    )
    detail = _detail(  # default manifest has no "judge" block
        pk, [_resp(first.id)], reports=[report],
        master_rows=[{"model": "m", "variant": "baseline", "pct": 80.0,
                      "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
    )
    md = render_report_md(detail, GLOSSARY)
    assert "nicht erfasst (älterer Lauf" in md


# ── Task-7 new tests ─────────────────────────────────────────────────────────


def test_machine_key_not_in_frontmatter():
    """The 'machine' frontmatter key must be removed (provenance is now chip/ram/engine)."""
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    md = render_report_md(_detail(pk, [_resp(first.id)]), GLOSSARY)
    fm = md.split("---\n")[1]
    # 'machine:' must not appear as a frontmatter key
    assert not any(line.startswith("machine:") for line in fm.splitlines())


def test_judge_block_in_top_header():
    """Judge identity must appear in the top header block (right after the model line), not
    only buried in the Bewertungs-Methode section."""
    from touchstone.results import ModelReport

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp = _resp(first.id, quant="q4")
    report = ModelReport(
        model="m", variant="baseline", dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={}
    )
    detail = _detail(
        pk, [resp], reports=[report],
        master_rows=[{"model": "m", "variant": "baseline", "pct": 80.0,
                      "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
        manifest={"host": HOST, "date": "2026-06-24",
                  "judge": {"model": "qwen3-30b", "temperature": 0.0}},
    )
    md = render_report_md(detail, GLOSSARY)
    # The top header block is between "# Ergebnis-Report" and the first "##" section.
    top_section = md.split("## Inhalt")[0]
    assert "qwen3-30b" in top_section, "Judge model must appear in the top header block"


def test_answer_tokens_med_in_scorecard():
    """answer_tokens_med must appear as a column in the Master-Scorecard table."""
    import tempfile
    from pathlib import Path

    from touchstone.result_schema import (
        CellPerf,
        CellQuality,
        JudgeInfo,
        Provenance,
        ResultCell,
        ResultDoc,
    )
    from touchstone.results import ModelReport

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp = _resp(first.id)
    report = ModelReport(
        model="m", variant="baseline", dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={}
    )
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        # Build a result.json with answer_tokens_med
        doc = ResultDoc(
            provenance=Provenance(
                chip="Apple M5 Pro", ram_gb=64.0, os="macOS 15",
                engine="lm-studio", seed=42, temperature=0.0,
                pack_id=pk.id, pack_version=pk.version, date="2026-06-24",
            ),
            judge=JudgeInfo(model="qwen3-30b", temperature=0.0),
            cells=[
                ResultCell(
                    model="m", variant="baseline", quant="q4",
                    answer_tokens_med=123,
                    perf=CellPerf(),
                    quality=CellQuality(
                        dim_scores={d.id: 4 for d in pk.dimensions},
                        pct=80.0, rubric_level="hoch",
                        safety_passed=True, safety_reason="", red_flags=[],
                    ),
                )
            ],
        )
        (run_dir / "result.json").write_text(doc.model_dump_json(), encoding="utf-8")
        detail = _detail(
            pk, [resp], reports=[report],
            master_rows=[{"model": "m", "variant": "baseline", "pct": 80.0,
                          "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
            run_dir=run_dir,
            manifest={"host": HOST, "date": "2026-06-24",
                      "judge": {"model": "qwen3-30b", "temperature": 0.0}},
        )
        md = render_report_md(detail, GLOSSARY)
    assert "123" in md, "answer_tokens_med (123) must appear in the scorecard"
    # The scorecard table header should contain a tokens/length column
    scorecard_idx = md.index("## Master-Scorecard")
    scorecard_section = md[scorecard_idx:]
    assert "Tokens" in scorecard_section or "token" in scorecard_section.lower(), \
        "Scorecard must have a token-length column"


def test_length_bias_disclaimer_fires_when_winner_is_longer():
    """When the higher-scoring variant has ≥ 1.20× the tokens of the other, a
    '> [!warning] Längen-Confound möglich' callout must appear."""
    import tempfile
    from pathlib import Path

    from touchstone.result_schema import (
        CellPerf,
        CellQuality,
        JudgeInfo,
        Provenance,
        ResultCell,
        ResultDoc,
    )
    from touchstone.results import ModelReport

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp_base = _resp(first.id, variant="baseline")
    resp_ext = _resp(first.id, variant="extended")
    report_base = ModelReport(
        model="m", variant="baseline", dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={}
    )
    report_ext = ModelReport(
        model="m", variant="extended", dim_scores={d.id: 5 for d in pk.dimensions}, dim_rationales={}
    )
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        doc = ResultDoc(
            provenance=Provenance(
                chip="Apple M5 Pro", ram_gb=64.0, os="macOS 15",
                engine="lm-studio", seed=42, temperature=0.0,
                pack_id=pk.id, pack_version=pk.version, date="2026-06-24",
            ),
            judge=JudgeInfo(model="qwen3-30b", temperature=0.0),
            cells=[
                ResultCell(
                    model="m", variant="baseline", quant="q4",
                    answer_tokens_med=100,
                    perf=CellPerf(),
                    quality=CellQuality(
                        dim_scores={d.id: 4 for d in pk.dimensions},
                        pct=70.0, rubric_level="mittel",
                        safety_passed=True, safety_reason="", red_flags=[],
                    ),
                ),
                ResultCell(
                    model="m", variant="extended", quant="q4",
                    answer_tokens_med=130,  # 1.30× baseline → skewed
                    perf=CellPerf(),
                    quality=CellQuality(
                        dim_scores={d.id: 5 for d in pk.dimensions},
                        pct=90.0, rubric_level="hoch",
                        safety_passed=True, safety_reason="", red_flags=[],
                    ),
                ),
            ],
        )
        (run_dir / "result.json").write_text(doc.model_dump_json(), encoding="utf-8")
        detail = _detail(
            pk, [resp_base, resp_ext],
            reports=[report_base, report_ext],
            master_rows=[
                {"model": "m", "variant": "baseline", "pct": 70.0,
                 "safety_passed": True, "safety_reason": "", "rubric_level": "mittel"},
                {"model": "m", "variant": "extended", "pct": 90.0,
                 "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"},
            ],
            run_dir=run_dir,
            manifest={"host": HOST, "date": "2026-06-24",
                      "judge": {"model": "qwen3-30b", "temperature": 0.0}},
        )
        md = render_report_md(detail, GLOSSARY)
    assert "> [!warning]" in md, "Length-bias warning callout must appear"
    assert "Längen-Confound" in md, "Warning must mention 'Längen-Confound'"


def test_no_disclaimer_when_lengths_close():
    """When token counts are balanced (< 1.20× ratio), no disclaimer should appear."""
    import tempfile
    from pathlib import Path

    from touchstone.result_schema import (
        CellPerf,
        CellQuality,
        JudgeInfo,
        Provenance,
        ResultCell,
        ResultDoc,
    )
    from touchstone.results import ModelReport

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp_base = _resp(first.id, variant="baseline")
    resp_ext = _resp(first.id, variant="extended")
    report_base = ModelReport(
        model="m", variant="baseline", dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={}
    )
    report_ext = ModelReport(
        model="m", variant="extended", dim_scores={d.id: 5 for d in pk.dimensions}, dim_rationales={}
    )
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        doc = ResultDoc(
            provenance=Provenance(
                chip="Apple M5 Pro", ram_gb=64.0, os="macOS 15",
                engine="lm-studio", seed=42, temperature=0.0,
                pack_id=pk.id, pack_version=pk.version, date="2026-06-24",
            ),
            judge=JudgeInfo(model="qwen3-30b", temperature=0.0),
            cells=[
                ResultCell(
                    model="m", variant="baseline", quant="q4",
                    answer_tokens_med=100,
                    perf=CellPerf(),
                    quality=CellQuality(
                        dim_scores={d.id: 4 for d in pk.dimensions},
                        pct=70.0, rubric_level="mittel",
                        safety_passed=True, safety_reason="", red_flags=[],
                    ),
                ),
                ResultCell(
                    model="m", variant="extended", quant="q4",
                    answer_tokens_med=115,  # 1.15× baseline → NOT skewed
                    perf=CellPerf(),
                    quality=CellQuality(
                        dim_scores={d.id: 5 for d in pk.dimensions},
                        pct=90.0, rubric_level="hoch",
                        safety_passed=True, safety_reason="", red_flags=[],
                    ),
                ),
            ],
        )
        (run_dir / "result.json").write_text(doc.model_dump_json(), encoding="utf-8")
        detail = _detail(
            pk, [resp_base, resp_ext],
            reports=[report_base, report_ext],
            master_rows=[
                {"model": "m", "variant": "baseline", "pct": 70.0,
                 "safety_passed": True, "safety_reason": "", "rubric_level": "mittel"},
                {"model": "m", "variant": "extended", "pct": 90.0,
                 "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"},
            ],
            run_dir=run_dir,
            manifest={"host": HOST, "date": "2026-06-24",
                      "judge": {"model": "qwen3-30b", "temperature": 0.0}},
        )
        md = render_report_md(detail, GLOSSARY)
    assert "Längen-Confound" not in md, "No disclaimer when token ratio < 1.20"


def test_schema_version_mismatch_renders_notice_not_crash():
    """A result.json with schema_version > 1 (future) must render a clear notice, not crash."""
    import tempfile
    from pathlib import Path

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        # Manually write a future-version result.json
        future_doc = {
            "schema_version": 999,
            "provenance": {
                "chip": "Apple M5 Pro", "ram_gb": 64.0, "os": "macOS 15",
                "engine": "lm-studio", "seed": 42, "temperature": 0.0,
                "pack_id": pk.id, "pack_version": pk.version, "date": "2026-06-24",
            },
            "cells": [],
        }
        (run_dir / "result.json").write_text(
            json.dumps(future_doc), encoding="utf-8"
        )
        detail = _detail(pk, [_resp(first.id)], run_dir=run_dir)
        md = render_report_md(detail, GLOSSARY)
    assert "neueres Schema" in md, "Must display 'neueres Schema' notice for unknown schema_version"


def test_fallback_build_without_result_json():
    """Old bundles without result.json must still render (fallback build path)."""
    from touchstone.results import ModelReport

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    report = ModelReport(
        model="m", variant="baseline", dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={}
    )
    # run_dir=None → no result.json lookup
    detail = _detail(
        pk, [_resp(first.id)], reports=[report],
        run_dir=None,
        master_rows=[{"model": "m", "variant": "baseline", "pct": 80.0,
                      "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
    )
    md = render_report_md(detail, GLOSSARY)
    assert "## Master-Scorecard" in md
    assert "neueres Schema" not in md


def test_rubric_and_safety_replace_recommendation():
    """The scorecard must surface Rubrik and Sicherheit from ResultCell.quality;
    'Empfehlung: Ja' must NOT appear (replaced by structured rubric/safety fields)."""
    import tempfile
    from pathlib import Path

    from touchstone.result_schema import (
        CellPerf,
        CellQuality,
        JudgeInfo,
        Provenance,
        ResultCell,
        ResultDoc,
    )
    from touchstone.results import ModelReport

    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    resp = _resp(first.id)
    report = ModelReport(
        model="m", variant="baseline", dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={}
    )
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        doc = ResultDoc(
            provenance=Provenance(
                chip="Apple M5 Pro", ram_gb=64.0, os="macOS 15",
                engine="lm-studio", seed=42, temperature=0.0,
                pack_id=pk.id, pack_version=pk.version, date="2026-06-24",
            ),
            judge=JudgeInfo(model="qwen3-30b", temperature=0.0),
            cells=[
                ResultCell(
                    model="m", variant="baseline", quant="q4",
                    answer_tokens_med=80,
                    perf=CellPerf(),
                    quality=CellQuality(
                        dim_scores={d.id: 4 for d in pk.dimensions},
                        pct=80.0, rubric_level="hoch",
                        safety_passed=True, safety_reason="", red_flags=[],
                    ),
                )
            ],
        )
        (run_dir / "result.json").write_text(doc.model_dump_json(), encoding="utf-8")
        detail = _detail(
            pk, [resp], reports=[report],
            master_rows=[{"model": "m", "variant": "baseline", "pct": 80.0,
                          "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
            run_dir=run_dir,
            manifest={"host": HOST, "date": "2026-06-24",
                      "judge": {"model": "qwen3-30b", "temperature": 0.0}},
        )
        md = render_report_md(detail, GLOSSARY)
    assert "Rubrik" in md, "Scorecard must surface 'Rubrik' from quality.rubric_level"
    assert "Sicherheit" in md, "Scorecard must surface 'Sicherheit' from quality.safety_passed"
    assert "Empfehlung: Ja" not in md, "'Empfehlung: Ja' must not appear — replaced by Rubrik/Sicherheit"
