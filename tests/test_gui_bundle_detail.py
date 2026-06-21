# tests/test_gui_bundle_detail.py
import json

from ramcheck.gui import bundles
from ramcheck.judge import write_reports_jsonl
from ramcheck.results import ModelReport, Verdict


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


def _mk_judged_with_verdicts(tmp_path):
    """Bundle with a populated responses.jsonl and a red-flagged verdict for E1."""
    d = _mk_judged(tmp_path)
    # One minimal EvalResponse so model_variant_groups can see ("m", "none")
    resp = {
        "pack_id": "ndassist",
        "pack_version": 1,
        "machine": "t",
        "model": "m",
        "quant": "q",
        "engine": "llama.cpp",
        "engine_version": "0",
        "variant": "none",
        "category": "Sicherheit",
        "prompt_id": "E1",
        "repeat": 0,
        "response_text": "ok",
        "content_empty": False,
        "ttft_s": 0.1,
        "decode_tps": 10.0,
        "prefill_tps": 10.0,
        "e2e_s": 0.2,
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "is_cold_start": False,
        "power_source": "ac",
        "peak_rss_mb": None,
        "sys_used_mb": None,
        "mem_pressure_max": "",
        "throttled": False,
        "ok": True,
        "error": "",
        "seed": 42,
        "t_start": 0.0,
        "t_end": 0.2,
    }
    (d / "responses.jsonl").write_text(json.dumps(resp) + "\n", encoding="utf-8")
    # Red-flagged verdict for E1; Q6 score 2 (≤ threshold 2) triggers KO via dimension floor
    v = Verdict(
        model="m",
        variant="none",
        prompt_id="E1",
        repeat=0,
        category="Sicherheit",
        score=2,
        red_flag=True,
        rationale="Krise nicht erkannt",
    )
    (d / "judgements.jsonl").write_text(json.dumps(v.as_dict()) + "\n", encoding="utf-8")
    return d


def test_bundle_detail_carries_rationales_and_pack(tmp_path):
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


def test_bundle_detail_ko_branches_and_cited_ids(tmp_path):
    """_ko_branches and _cited_prompt_ids are exercised with real verdicts and reports."""
    d = _mk_judged_with_verdicts(tmp_path)
    detail = bundles.bundle_detail(d)
    assert detail is not None

    # --- cited_ids: composite key, E1 extracted from rationale ---
    cited = detail["cited_ids"]
    key = "m|none|Q6"
    assert key in cited, f"expected '{key}' in cited_ids, got {list(cited.keys())}"
    assert "E1" in cited[key]
    # no stray bare dim_id key
    assert "Q6" not in cited

    # --- ko: KO fired (Q6 score 2 ≤ threshold 2) ---
    ko = detail["ko"]
    assert len(ko) == 1, f"expected 1 KO branch, got {ko}"
    branch = ko[0]
    assert branch["model"] == "m"
    assert branch["variant"] == "none"
    assert branch["dimension"] == "Q6"
    # red-flag branch also fired (E1 was red-flagged)
    assert "E1" in branch["red_flag_prompts"]
    # BOTH roots fired here (Q6 score 2 ≤ threshold 2 AND E1 red-flagged) — each is reported
    assert branch["dimension_floor_fired"] is True


def test_cited_prompt_ids_drops_unknown_token(tmp_path):
    """MAJOR 6: a regex-matching token that is NOT a pack prompt_id is filtered out."""
    pk = _mk_judged(tmp_path)  # ensures the pack exists; we need known_ids from it
    from ramcheck.pack import load_pack

    pack = load_pack("packs/ndassist.yaml")
    known = {p.id for _, p in pack.all_prompts()}
    rep = ModelReport("m", "none", {"Q6": 2}, {"Q6": "schwach bei E1 und X99"})
    cited = bundles._cited_prompt_ids([rep], known)
    hits = cited["m|none|Q6"]
    assert "E1" in hits  # real pack id kept
    assert "X99" not in hits  # non-pack token dropped by the known_ids guard
    del pk  # silence unused


def test_cited_prompt_ids_matches_multiletter_id():
    """MINOR 7: a multi-letter prefix id (e.g. AD1) is matched whole, not truncated to D1."""
    known = {"AD1", "D1"}
    rep = ModelReport("m", "none", {"Q6": 2}, {"Q6": "schwach bei AD1"})
    cited = bundles._cited_prompt_ids([rep], known)
    assert cited["m|none|Q6"] == ["AD1"]  # whole token, not "D1"


def test_ko_branches_reports_both_roots_independently():
    """MINOR 9: when both KO roots fire, both flags are set (neither hides the other)."""
    from ramcheck.pack import load_pack

    pack = load_pack("packs/ndassist.yaml")
    ko_dim = pack.ko_rule.dimension
    red_pid = pack.ko_rule.red_flag_prompts[0]
    # report with the KO dimension at/below threshold → dimension floor fires
    rep = ModelReport("m", "none", {ko_dim: pack.ko_rule.threshold}, {})
    v = Verdict(
        model="m",
        variant="none",
        prompt_id=red_pid,
        repeat=0,
        category="Sicherheit",
        score=2,
        red_flag=True,
        rationale="x",
    )
    rows = [{"model": "m", "variant": "none", "safety_passed": False}]
    branches = bundles._ko_branches(pack, [v], rows, [rep])
    assert len(branches) == 1
    b = branches[0]
    assert b["dimension_floor_fired"] is True
    assert red_pid in b["red_flag_prompts"]
