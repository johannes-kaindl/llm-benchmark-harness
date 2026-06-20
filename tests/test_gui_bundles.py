import json
import os

import pytest

from ramcheck.gui import bundles


def _mk(d, *, bundle=False, scores=False, responses=False, sentinel_state=None):
    d.mkdir(parents=True, exist_ok=True)
    if bundle:
        (d / "bundle.json").write_text(
            json.dumps(
                {
                    "pack_id": "ndassist",
                    "pack_path": "packs/ndassist.yaml",
                    "models": [{"id": "qwen2.5:3b", "quant": "q"}],
                    "date": "2026-06-20",
                }
            ),
            encoding="utf-8",
        )
    if responses:
        (d / "responses.jsonl").write_text("", encoding="utf-8")
    if scores:
        (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    if sentinel_state:
        (d / "run.json").write_text(
            json.dumps({"kind": "eval", "pid": 1, "state": sentinel_state, "run_dir": str(d)}),
            encoding="utf-8",
        )


def test_classify_judged(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _mk(d, bundle=True, responses=True, scores=True)
    s = bundles.classify(d)
    assert s.status == "judged"


def test_classify_eval_only(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _mk(d, bundle=True, responses=True)
    assert bundles.classify(d).status == "eval-only"


def test_classify_crashed(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _mk(d, responses=True, sentinel_state="failed")
    assert bundles.classify(d).status == "crashed"


def test_legacy_run_dir_ignored(tmp_path):
    d = tmp_path / "2026_plainrun"
    d.mkdir()
    (d / "raw.csv").write_text("x\n", encoding="utf-8")
    assert bundles.classify(d) is None


def test_discover_lists_only_bundles(tmp_path):
    _mk(tmp_path / "a_eval_nd", bundle=True, responses=True, scores=True)
    (tmp_path / "legacy").mkdir()
    (tmp_path / "legacy" / "raw.csv").write_text("x\n", encoding="utf-8")
    found = bundles.discover(tmp_path)
    assert [b.run_dir.name for b in found] == ["a_eval_nd"]


REAL = "runs/2026-06-20_104844_eval_ndassist"


@pytest.mark.skipif(not os.path.isdir(REAL), reason="real bundle not present")
def test_recompute_verdict_real_bundle():
    from pathlib import Path

    s = bundles.classify(Path(REAL))
    assert s is not None and s.status == "judged"
    assert s.recommendation in {"Ja", "Mit Einschränkung", "Nein"}
