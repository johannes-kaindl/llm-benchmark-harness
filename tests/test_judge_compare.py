from pathlib import Path

from touchstone.gui.judge_compare import (
    PACK_UNKNOWN,
    judge_quality_rows,
    parse_frontmatter,
)

FM = (
    '---\ntype: "judge_quality"\nbundle: b1\npack: ndassist\n'
    'judge_model: "qwen3-27b"\nmean_abs_delta: 0.5\nnames_improvement_rate: 0.75\n'
    "cites_evidence_rate: 0.5\njustifies_level_rate: 1.0\ncatches_safety_rate: 0.25\n---\n# body\n"
)


def test_parse_frontmatter_valid():
    fm = parse_frontmatter(FM)
    assert fm is not None
    assert fm["type"] == "judge_quality" and fm["pack"] == "ndassist"
    assert fm["mean_abs_delta"] == 0.5


def test_parse_frontmatter_no_block():
    assert parse_frontmatter("# just a heading\nno frontmatter here\n") is None


def test_parse_frontmatter_malformed_yaml():
    # unterminated/garbage YAML inside a --- block → None, never raises
    assert parse_frontmatter("---\n: : : not yaml : :\n[unclosed\n---\n") is None


def test_parse_frontmatter_non_mapping():
    assert parse_frontmatter("---\n- a\n- b\n---\n") is None


def _write_jq(d: Path, text: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "judge_quality.md").write_text(text, encoding="utf-8")


def test_judge_quality_rows_discovers_and_parses(tmp_path):
    _write_jq(tmp_path / "b1", FM)
    _write_jq(
        tmp_path / "b2",
        '---\ntype: "judge_quality"\nbundle: b2\npack: buero\n'
        'judge_model: "gpt-x"\nmean_abs_delta: 1.2\nnames_improvement_rate: 0.3\n'
        "cites_evidence_rate: 0.4\njustifies_level_rate: 0.6\ncatches_safety_rate: 0.9\n---\n",
    )
    rows = judge_quality_rows(tmp_path)
    assert len(rows) == 2
    by = {r.bundle: r for r in rows}
    assert by["b1"].pack == "ndassist" and by["b1"].judge_model == "qwen3-27b"
    assert by["b1"].mean_abs_delta == 0.5 and by["b1"].cites_evidence_rate == 0.5
    assert by["b1"].key == "b1|qwen3-27b"


def test_judge_quality_rows_skips_malformed_and_foreign(tmp_path):
    _write_jq(tmp_path / "good", FM)
    _write_jq(tmp_path / "broken", "not a frontmatter file at all\n")
    _write_jq(tmp_path / "foreign", '---\ntype: "something_else"\nbundle: z\n---\n')
    rows = judge_quality_rows(tmp_path)
    assert [r.bundle for r in rows] == ["b1"]  # broken + foreign skipped; FM has bundle: b1


def test_judge_quality_rows_missing_rates_and_pack(tmp_path):
    # an old judge_quality.md without pack / the 3 new rates → PACK_UNKNOWN + None rates
    _write_jq(
        tmp_path / "old",
        '---\ntype: "judge_quality"\nbundle: old\njudge_model: "j"\n'
        "mean_abs_delta: 0.4\nnames_improvement_rate: 0.5\n---\n",
    )
    rows = judge_quality_rows(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r.pack == PACK_UNKNOWN
    assert r.cites_evidence_rate is None and r.justifies_level_rate is None


def test_judge_quality_rows_skips_trash(tmp_path):
    _write_jq(tmp_path / "live", FM)
    _write_jq(tmp_path / ".trash" / "deleted", FM)
    rows = judge_quality_rows(tmp_path)
    assert [r.bundle for r in rows] == ["b1"]  # the .trash copy is skipped (bundle field b1)
