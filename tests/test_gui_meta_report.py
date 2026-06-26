from touchstone.aggregate import PoolRow
from touchstone.gui.meta_report import filter_detail_to_cells, select_rows


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
