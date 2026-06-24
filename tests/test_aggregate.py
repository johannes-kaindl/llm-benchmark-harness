import csv

from ramcheck import aggregate as agg

HEADER = [
    "machine",
    "chip",
    "ram_gb",
    "pack",
    "pack_version",
    "model",
    "quant",
    "variant",
    "ttft_p50",
    "decode_med",
    "e2e_med",
    "peak_ram_gb",
    "model_delta_gb",
    "power",
    "metric_type",
    "metric",
    "weight",
    "score",
]


def _row(**kw):
    base = {h: "" for h in HEADER}
    base.update(kw)
    return base


def _write_scores(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_load_all_scores_concatenates_recursively(tmp_path):
    _write_scores(
        tmp_path / "runs" / "a" / "scores.csv",
        [_row(model="m1", metric_type="dimension", metric="Q1", weight="3", score="4")],
    )
    _write_scores(
        tmp_path / "runs" / "b" / "scores.csv",
        [_row(model="m2", metric_type="dimension", metric="Q1", weight="3", score="2")],
    )
    rows = agg.load_all_scores(tmp_path / "runs")
    assert len(rows) == 2
    assert {r["model"] for r in rows} == {"m1", "m2"}


def test_load_all_scores_missing_dir():
    assert agg.load_all_scores("/nonexistent/xyz") == []


def test_aggregate_computes_weighted_quality():
    # Q1 weight 3 score 4, Q2 weight 1 score 2 → wsum=14, wmax=5*(3+1)=20 → 70%
    rows = [
        _row(
            chip="M5",
            machine="mac",
            pack="nd",
            pack_version="1",
            model="m",
            variant="baseline",
            ttft_p50="0.3",
            decode_med="60",
            peak_ram_gb="40",
            power="ac",
            metric_type="dimension",
            metric="Q1",
            weight="3",
            score="4",
        ),
        _row(
            chip="M5",
            machine="mac",
            pack="nd",
            pack_version="1",
            model="m",
            variant="baseline",
            metric_type="dimension",
            metric="Q2",
            weight="1",
            score="2",
        ),
    ]
    out = agg.aggregate(rows)
    assert len(out) == 1
    a = out[0]
    assert a.quality_pct == 70.0
    assert a.n_dims == 2
    assert a.dim_scores == {"Q1": 4, "Q2": 2}
    assert a.ttft_p50 == "0.3" and a.decode_med == "60"


def test_aggregate_carries_model_delta_gb():
    rows = [
        _row(
            chip="M5",
            machine="mac",
            model="m",
            variant="baseline",
            peak_ram_gb="50.8",
            model_delta_gb="11.7",
            metric_type="dimension",
            metric="Q1",
            weight="3",
            score="4",
        )
    ]
    out = agg.aggregate(rows)
    assert out[0].model_delta_gb == "11.7"
    md = agg.render_aggregate_md(out)
    assert "Modell-Delta (GB)" in md  # new column header
    assert "11.7" in md


def test_aggregate_unscored_group_has_no_quality():
    rows = [_row(model="m", variant="v", metric_type="none", metric="", weight="", score="")]
    out = agg.aggregate(rows)
    assert out[0].quality_pct is None and out[0].n_dims == 0


def test_aggregate_sorts_by_chip_then_quality_desc():
    rows = [
        _row(
            chip="M5",
            model="lo",
            variant="v",
            metric_type="dimension",
            metric="Q1",
            weight="1",
            score="2",
        ),  # 40%
        _row(
            chip="M5",
            model="hi",
            variant="v",
            metric_type="dimension",
            metric="Q1",
            weight="1",
            score="5",
        ),  # 100%
        _row(
            chip="A1",
            model="x",
            variant="v",
            metric_type="dimension",
            metric="Q1",
            weight="1",
            score="3",
        ),  # 60%
    ]
    out = agg.aggregate(rows)
    assert [a.model for a in out] == ["x", "hi", "lo"]  # A1 first, then M5 by quality desc


def test_render_aggregate_md_has_table():
    rows = [
        _row(
            chip="M5",
            machine="mac",
            pack="nd",
            pack_version="1",
            model="m",
            variant="baseline",
            metric_type="dimension",
            metric="Q1",
            weight="3",
            score="4",
        )
    ]
    md = agg.render_aggregate_md(agg.aggregate(rows))
    assert "Hardware × Qualität" in md
    assert "| m |" in md
    assert "Qualität %" in md


def test_render_aggregate_md_graceful_with_empty_identity_fields():
    rows = [_row(metric_type="dimension", metric="Q1", weight="1", score="3")]  # all identity ""
    md = agg.render_aggregate_md(agg.aggregate(rows))
    data = [
        ln for ln in md.splitlines() if ln.startswith("| ") and "Chip" not in ln and ":-:" not in ln
    ]
    assert len(data) == 1
    cells = data[0].split("|")
    assert cells[4].strip() == "—"  # model → em-dash, not blank
    assert "v" not in cells[7]  # pack cell is "—", not a malformed " v"
    assert "60.0" in data[0]  # quality % = 3 / (5*1) = 60


def test_write_scores_all_csv_roundtrip(tmp_path):
    rows = [_row(model="m1", metric="Q1"), _row(model="m2", metric="Q2")]
    p = tmp_path / "scores_all.csv"
    agg.write_scores_all_csv(rows, p)
    back = list(csv.DictReader(p.open(encoding="utf-8")))
    assert len(back) == 2 and {r["model"] for r in back} == {"m1", "m2"}
