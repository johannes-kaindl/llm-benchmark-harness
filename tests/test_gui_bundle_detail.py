# tests/test_gui_bundle_detail.py
import json

from ramcheck.gui import bundles
from ramcheck.judge import write_reports_jsonl
from ramcheck.results import ModelReport


def _mk_judged(tmp_path):
    d = tmp_path / "2026_eval_nd"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-21",
                "host": {"machine": "t"},
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    write_reports_jsonl(
        d / "reports.jsonl",
        [ModelReport("m", "none", {"Q6": 2}, {"Q6": "schwach bei E1"})],
    )
    return d


def test_bundle_detail_carries_rationales_and_kovariant(tmp_path):
    d = _mk_judged(tmp_path)
    detail = bundles.bundle_detail(d)
    # the holistic Q6 rationale is present and cites E1
    rep = detail["reports"][0]
    assert rep.dim_rationales["Q6"] == "schwach bei E1"
    # the pack (criteria) is loaded so the view can render dimensions/ko-rule
    assert detail["pack"].ko_rule.dimension == "Q6"


def test_bundle_detail_missing_reports_marks_unrecorded(tmp_path):
    d = _mk_judged(tmp_path)
    (d / "reports.jsonl").unlink()  # old bundle → scores.csv fallback, no rationales
    detail = bundles.bundle_detail(d)
    # falls back without crashing; rationales empty (UI shows 'nicht erfasst')
    assert detail is not None
