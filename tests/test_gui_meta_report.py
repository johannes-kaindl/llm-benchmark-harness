import csv
import io

from touchstone.aggregate import PoolRow
from touchstone.gui.meta_report import filter_detail_to_cells, render_meta_leaderboard_csv, select_rows


def _pr(run_name, model, variant, quality=40.0):
    return PoolRow(id=f"{run_name}|{model}|{variant}", run_name=run_name, chip="M5", ram_gb="64",
                   pack="ndassist", pack_version="1", model=model, quant="q4", variant=variant,
                   quality_pct=quality, ttft_p50="0.40", decode_med="18.2",
                   peak_ram_gb="20.0", model_delta_gb="4.1", power="ac")


def test_select_rows_filters_and_orders():
    pool = [_pr("r1", "a", "baseline"), _pr("r1", "b", "baseline"), _pr("r2", "a", "baseline")]
    out = select_rows(pool, ["r2|a|baseline", "r1|b|baseline"])
    assert [r.id for r in out] == ["r2|a|baseline", "r1|b|baseline"]  # selection order preserved


def test_select_rows_empty_or_none():
    pool = [_pr("r1", "a", "baseline")]
    assert select_rows(pool, None) == []
    assert select_rows(pool, ["", "nope|x|y"]) == []


class _R:
    def __init__(self, model, variant):
        self.model, self.variant = model, variant


def test_filter_detail_to_cells_narrows_only_cell_keyed_lists():
    detail = {
        "pack": "PK", "manifest": {"x": 1}, "perf": {"p": 2}, "run_dir": None,
        "responses": [_R("a", "baseline"), _R("a", "none"), _R("b", "baseline")],
        "verdicts": [_R("a", "baseline"), _R("b", "baseline")],
        "reports": [_R("a", "baseline"), _R("a", "none")],
        "master_rows": [{"model": "a", "variant": "baseline"}, {"model": "b", "variant": "baseline"}],
        "cited_ids": {"a|baseline|d1": ["p1"], "b|baseline|d1": ["p2"], "a|none|d1": ["p3"]},
    }
    out = filter_detail_to_cells(detail, {("a", "baseline")})
    assert [(r.model, r.variant) for r in out["responses"]] == [("a", "baseline")]
    assert [(r.model, r.variant) for r in out["verdicts"]] == [("a", "baseline")]
    assert [(r.model, r.variant) for r in out["reports"]] == [("a", "baseline")]
    assert out["master_rows"] == [{"model": "a", "variant": "baseline"}]
    assert out["cited_ids"] == {"a|baseline|d1": ["p1"]}
    # untouched keys pass through, original not mutated
    assert out["pack"] == "PK" and out["perf"] == {"p": 2}
    assert len(detail["responses"]) == 3


def test_leaderboard_csv_one_row_per_cell():
    out = render_meta_leaderboard_csv([_pr("r1", "a", "baseline", quality=40.0),
                                       _pr("r2", "b", "none", quality=None)])
    reader = list(csv.DictReader(io.StringIO(out)))
    assert reader[0]["run_name"] == "r1" and reader[0]["model"] == "a"
    assert reader[0]["quality_pct"] == "40.0"
    assert reader[1]["quality_pct"] == ""  # None → empty cell
    assert reader[0]["decode_med"] == "18.2" and reader[0]["model_delta_gb"] == "4.1"
    # exact column order
    assert list(reader[0].keys())[0] == "run_name"


from touchstone.gui.glossary import GLOSSARY
from touchstone.gui.meta_report import render_meta_report_md
from touchstone.pack import load_pack
from touchstone.results import EvalResponse, ModelReport, Verdict

PACK = "packs/ndassist.yaml"
HOST = {"chip": "Apple M5 Pro", "ram_gb": "64.0 GB", "engine": "lm-studio"}


def _resp(pid, model="m", variant="baseline", **over):
    base = dict(pack_id="ndassist", pack_version=1, machine="t", model=model, quant="q4",
                engine="lm-studio", engine_version="0", variant=variant, category="A",
                prompt_id=pid, repeat=0, response_text="Antwort.", content_empty=False,
                ttft_s=0.2, decode_tps=30.0, prefill_tps=90.0, e2e_s=1.5, prompt_tokens=100,
                completion_tokens=50, is_cold_start=False, power_source="ac", peak_rss_mb=0.0,
                sys_used_mb=20000.0, mem_pressure_max="normal", throttled=False, ok=True,
                error="", seed=42, t_start=0.0, t_end=1.5, reasoning_chars=0)
    base.update(over)
    return EvalResponse(**base)


def _detail(model="m", variant="baseline", run_name="r1"):
    from pathlib import Path
    pk = load_pack(PACK)
    first = next(p for _, p in pk.all_prompts())
    rep = ModelReport(model=model, variant=variant,
                      dim_scores={d.id: 4 for d in pk.dimensions}, dim_rationales={})
    ver = Verdict(model=model, variant=variant, prompt_id=first.id, repeat=0, category="A",
                  score=4, red_flag=False, rationale="gut", unscored=False, safety_critical=False)
    return {"run_dir": Path(f"runs/{run_name}"), "manifest": {"host": HOST, "date": "2026-06-24"},
            "pack": pk, "responses": [_resp(first.id, model, variant)], "verdicts": [ver],
            "reports": [rep],
            "master_rows": [{"model": model, "variant": variant, "pct": 80.0,
                             "safety_passed": True, "safety_reason": "", "rubric_level": "hoch"}],
            "cited_ids": {}, "perf": {}}


def test_meta_report_judged_has_summary_and_detail():
    sel = [_pr("r1", "m", "baseline", 80.0), _pr("r1", "m", "none", 40.0)]
    md = render_meta_report_md(sel, [_detail("m", "baseline"), _detail("m", "none")],
                               GLOSSARY, include_judging=True)
    assert md.startswith("---\n") and "type: \"meta_report\"" in md
    assert "## Summary" in md and "## Detail" in md
    assert "Quality" in md.split("## Detail")[0]          # quality column present in summary
    assert "## Bewertungs-Methode" in md and "## Metrik-Glossar" in md
    assert md.count("## Metrik-Glossar") == 1             # glossary exactly once


def test_meta_report_blank_hides_quality_everywhere():
    sel = [_pr("r1", "m", "baseline", 80.0), _pr("r1", "m", "none", 40.0)]
    md = render_meta_report_md(sel, [_detail("m", "baseline"), _detail("m", "none")],
                               GLOSSARY, include_judging=False)
    summary = md.split("## Detail")[0]
    assert "Quality" not in summary                        # quality column dropped
    assert "80" not in summary                             # no leaked score
    assert "## 📋 Bewertungs-Auftrag" in md or "Vorlage:" in md
    assert "## Master-Scorecard" not in md


def test_meta_report_single_cell_no_trophy():
    md = render_meta_report_md([_pr("r1", "m", "baseline", 80.0)], [_detail("m", "baseline")],
                               GLOSSARY, include_judging=True)
    assert "🏆" not in md.split("## Detail")[0]            # one cell → no winners
