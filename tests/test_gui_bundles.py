import json
import os

import pytest

from touchstone.gui import bundles


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


def test_classify_running_judge_sets_run_kind(tmp_path):
    # a finalized eval bundle with a JUDGE running on it → the live card must tail the judge stream
    d = tmp_path / "2026_eval_nd"
    _mk(d, bundle=True, responses=True)
    (d / "run.json").write_text(
        json.dumps({"kind": "judge", "pid": 1, "state": "running", "run_dir": str(d)}),
        encoding="utf-8",
    )
    s = bundles.classify(d)
    assert s.status == "running"
    assert s.run_kind == "judge"


def test_classify_running_eval_defaults_run_kind_eval(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _mk(d, responses=True, sentinel_state="running")  # _mk writes kind="eval"
    s = bundles.classify(d)
    assert s.status == "running"
    assert s.run_kind == "eval"


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


PACK = "packs/ndassist.yaml"


def _resp_dict(model, variant):
    """One EvalResponse round-trip dict for responses.jsonl."""
    from touchstone.results import EvalResponse

    return EvalResponse(
        pack_id="ndassist",
        pack_version=1,
        machine="t",
        model=model,
        quant="q",
        engine="e",
        engine_version="x",
        variant=variant,
        category="A",
        prompt_id="A1",
        repeat=0,
        response_text="ok",
        content_empty=False,
        ttft_s=0.1,
        decode_tps=1.0,
        prefill_tps=1.0,
        e2e_s=1.0,
        prompt_tokens=1,
        completion_tokens=1,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=0.0,
        sys_used_mb=0.0,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.0,
        reasoning_chars=0,
    ).as_dict()


def _write_bundle(d, *, groups, scores_by_group, blank_dims=()):
    """Build a self-contained judged bundle under d using the real in-repo pack.

    groups: list of (model, variant).
    scores_by_group: dict (model, variant) -> dict(dim_id -> score) (full master scores).
    blank_dims: iterable of dim ids to emit with score='' (judge omitted them).
    """
    from touchstone.pack import load_pack

    d.mkdir(parents=True, exist_ok=True)
    pk = load_pack(PACK)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "models": [{"id": g[0], "quant": "q"} for g in groups],
                "date": "2026-06-20",
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text(
        "\n".join(json.dumps(_resp_dict(m, v)) for (m, v) in groups) + "\n",
        encoding="utf-8",
    )
    header = ["model", "variant", "metric_type", "metric", "weight", "score"]
    lines = [",".join(header)]
    for m, v in groups:
        scores = scores_by_group[(m, v)]
        for dim in pk.dimensions:
            raw = "" if dim.id in blank_dims else str(scores.get(dim.id, ""))
            lines.append(f"{m},{v},dimension,{dim.id},{dim.weight},{raw}")
    (d / "scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pk


def test_reports_from_scores_skips_blank_score(tmp_path):
    """A judge that omitted a master dimension (score='') must not crash classify."""
    from touchstone.pack import load_pack

    d = tmp_path / "2026_eval_nd"
    pk = load_pack(PACK)
    _write_bundle(
        d,
        groups=[("m", "baseline")],
        scores_by_group={("m", "baseline"): {dim.id: 5 for dim in pk.dimensions}},
        blank_dims={"Q7"},  # judge omitted Q7 → score=''
    )
    # must not raise (was: int(float('')) -> ValueError -> HTTP 500)
    s = bundles.classify(d)
    assert s is not None and s.status == "judged"
    # the partial report is still valid; Q7 just absent from dim_scores
    reports = bundles._reports_from_scores(d, pk)
    assert reports and "Q7" not in reports[0].dim_scores


def test_recompute_verdict_hermetic_ja(tmp_path):
    """Hermetic (no skip): full master scores all 5 → 'Ja', safety passes."""
    from touchstone.pack import load_pack

    pk = load_pack(PACK)
    d = tmp_path / "2026_eval_nd"
    _write_bundle(
        d,
        groups=[("m", "baseline")],
        scores_by_group={("m", "baseline"): {dim.id: 5 for dim in pk.dimensions}},
    )
    s = bundles.classify(d)
    assert s is not None and s.status == "judged"
    assert s.recommendation == "Ja"
    assert s.safety_passed is True


def test_recompute_verdict_badge_picks_strongest(tmp_path):
    """Two groups: one all-5 ('Ja'), one with Q6 (K.-o.) at 2 ('Nein').

    The badge must report the STRONGEST recommendation via the order map → 'Ja'.
    """
    from touchstone.pack import load_pack

    pk = load_pack(PACK)
    full = {dim.id: 5 for dim in pk.dimensions}
    ko = dict(full)
    ko["Q6"] = 2  # K.-o. dimension at/below threshold → that group is 'Nein'
    d = tmp_path / "2026_eval_nd"
    _write_bundle(
        d,
        groups=[("good", "baseline"), ("bad", "baseline")],
        scores_by_group={
            ("good", "baseline"): full,
            ("bad", "baseline"): ko,
        },
    )
    s = bundles.classify(d)
    assert s is not None and s.status == "judged"
    # strongest wins: 'Ja' beats 'Nein'
    assert s.recommendation == "Ja"


REAL = "runs/2026-06-20_104844_eval_ndassist"


@pytest.mark.skipif(not os.path.isdir(REAL), reason="real bundle not present")
def test_recompute_verdict_real_bundle():
    from pathlib import Path

    s = bundles.classify(Path(REAL))
    assert s is not None and s.status == "judged"
    assert s.recommendation in {"Ja", "Mit Einschränkung", "Nein"}
