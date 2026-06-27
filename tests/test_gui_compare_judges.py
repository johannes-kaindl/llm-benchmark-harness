import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry


class _L:
    def spawn(self, a):
        return 1

    def alive(self, p):
        return False

    def terminate(self, p):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_L())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _jq(d, bundle, pack, judge, mad, ni, ce, jl, cs):
    d.mkdir(parents=True, exist_ok=True)
    (d / "judge_quality.md").write_text(
        f'---\ntype: "judge_quality"\nbundle: {bundle}\npack: {pack}\n'
        f'judge_model: "{judge}"\nmean_abs_delta: {mad}\nnames_improvement_rate: {ni}\n'
        f"cites_evidence_rate: {ce}\njustifies_level_rate: {jl}\ncatches_safety_rate: {cs}\n---\n# x\n",
        encoding="utf-8",
    )


def test_compare_judges_renders_grouped_table(tmp_path):
    _jq(tmp_path / "b1", "b1", "ndassist", "judgeA", 0.5, 0.7, 0.8, 0.6, 0.9)
    _jq(tmp_path / "b2", "b2", "ndassist", "judgeB", 0.2, 0.4, 0.4, 0.5, 0.5)
    r = _client(tmp_path).get("/compare-judges")
    assert r.status_code == 200
    assert "ndassist" in r.text
    assert "judgeA" in r.text and "judgeB" in r.text
    assert "🏆" in r.text  # at least one per-metric winner highlighted


def test_compare_judges_empty_state(tmp_path):
    r = _client(tmp_path).get("/compare-judges")
    assert r.status_code == 200
    assert "judge_quality" in r.text.lower() or "noch keine" in r.text.lower()


def test_compare_judges_tolerates_malformed_file(tmp_path):
    _jq(tmp_path / "ok", "ok", "p", "j", 0.5, 0.5, 0.5, 0.5, 0.5)
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "judge_quality.md").write_text(
        "garbage, no frontmatter\n", encoding="utf-8"
    )
    r = _client(tmp_path).get("/compare-judges")
    assert r.status_code == 200  # malformed file skipped, never 500
    assert "judge_quality" in r.text.lower() or "ok" in r.text
