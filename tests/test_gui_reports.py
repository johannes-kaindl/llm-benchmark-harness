# tests/test_gui_reports.py
import json
import types

from typer.testing import CliRunner

from touchstone.cli import app
from touchstone.judge import load_reports_jsonl, write_reports_jsonl
from touchstone.results import ModelReport

runner = CliRunner()


def test_reports_jsonl_roundtrip(tmp_path):
    reports = [
        ModelReport("m", "baseline", {"Q1": 4, "Q6": 2}, {"Q1": "ok", "Q6": "E1 schwach"}),
        ModelReport("m", "none", {"Q1": 3}, {"Q1": "knapp"}),
    ]
    p = tmp_path / "reports.jsonl"
    write_reports_jsonl(p, reports)
    back = load_reports_jsonl(p)
    assert len(back) == 2
    assert back[0].dim_scores == {"Q1": 4, "Q6": 2}
    assert back[0].dim_rationales["Q6"] == "E1 schwach"


def test_load_reports_jsonl_tolerates_half_line(tmp_path):
    p = tmp_path / "reports.jsonl"
    p.write_text(
        '{"model":"m","variant":"none","dim_scores":{"Q1":3},"dim_rationales":{}}\n{"model":"m"',
        encoding="utf-8",
    )
    back = load_reports_jsonl(p)
    assert len(back) == 1 and back[0].dim_scores == {"Q1": 3}


def test_missing_reports_jsonl_returns_empty(tmp_path):
    assert load_reports_jsonl(tmp_path / "nope.jsonl") == []


def test_judge_writes_reports_jsonl(tmp_path, monkeypatch):
    b = tmp_path / "bundle"
    b.mkdir()
    (b / "bundle.json").write_text(
        json.dumps({"pack_path": "packs/ndassist.yaml", "host": {}}), encoding="utf-8"
    )
    (b / "responses.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr("touchstone.cli.load_responses_jsonl", lambda p: [])
    monkeypatch.setattr(
        "touchstone.cli._judge_and_persist",
        lambda *a, **k: ([], [ModelReport("m", "none", {"Q1": 3}, {"Q1": "x"})]),
    )
    monkeypatch.setattr("touchstone.cli.OpenAIJudgeBackend", lambda *a, **k: object())
    monkeypatch.setattr(
        "touchstone.cli.load_judge_config",
        lambda p: types.SimpleNamespace(
            endpoint=types.SimpleNamespace(base_url="x", api_key="y"),
            model="m",
            temperature=0.0,
            call_timeout_s=120,
            max_consecutive_failures=3,
            max_tokens=None,
        ),
    )
    res = runner.invoke(app, ["judge", "--bundle", str(b), "--judge-config", "judge.yaml"])
    assert res.exit_code == 0, res.output
    assert (b / "reports.jsonl").exists()
    assert load_reports_jsonl(b / "reports.jsonl")[0].dim_rationales["Q1"] == "x"
