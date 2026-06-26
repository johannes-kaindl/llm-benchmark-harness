"""Golden characterization test: pins render_report_md's exact output so the
section-extraction refactor (meta-report sub-project E) is provably byte-preserving.
First run writes the golden + skips; commit it; later runs assert byte-equality."""

from pathlib import Path

import pytest

from touchstone.gui.glossary import GLOSSARY
from touchstone.gui.report_md import render_report_md
from touchstone.pack import load_pack
from touchstone.results import EvalResponse, ModelReport, Verdict

PACK = "packs/ndassist.yaml"
HOST = {"chip": "Apple M5 Pro", "ram_gb": "64.0 GB", "machine": "M5-64GB", "engine": "lm-studio"}
GOLDEN = Path(__file__).parent / "golden"


def _resp(prompt_id, **over):
    base = dict(
        pack_id="ndassist",
        pack_version=1,
        machine="t",
        model="m",
        quant="q4",
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
    return EvalResponse(**base)


def _detail():
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    report = ModelReport(
        model="m",
        variant="baseline",
        dim_scores={d.id: 4 for d in pk.dimensions},
        dim_rationales={},
    )
    verdict = Verdict(
        model="m",
        variant="baseline",
        prompt_id=first.id,
        repeat=0,
        category="A",
        score=4,
        red_flag=False,
        rationale="gut",
        unscored=False,
        safety_critical=False,
    )
    return {
        "run_dir": None,
        "manifest": {
            "host": HOST,
            "date": "2026-06-24",
            "judge": {"model": "qwen3-30b", "temperature": 0.0},
        },
        "pack": pk,
        "responses": [_resp(first.id)],
        "verdicts": [verdict],
        "reports": [report],
        "master_rows": [
            {
                "model": "m",
                "variant": "baseline",
                "pct": 80.0,
                "safety_passed": True,
                "safety_reason": "",
                "rubric_level": "hoch",
            }
        ],
        "cited_ids": {},
        "perf": {},
    }


def _check(name: str, md: str) -> None:
    GOLDEN.mkdir(exist_ok=True)
    p = GOLDEN / f"{name}.md"
    if not p.exists():
        p.write_text(md, encoding="utf-8")
        pytest.skip(f"wrote golden {name} (commit it, then re-run)")
    assert md == p.read_text(encoding="utf-8"), f"render_report_md output drifted vs golden {name}"


def test_golden_judged():
    _check("report_md_judged", render_report_md(_detail(), GLOSSARY, include_judging=True))


def test_golden_blank():
    _check("report_md_blank", render_report_md(_detail(), GLOSSARY, include_judging=False))
