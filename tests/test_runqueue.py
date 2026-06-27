from pathlib import Path

import pytest

from touchstone import runqueue as rq


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def _cfg(tmp_path: Path) -> Path:
    return _write(tmp_path / "c.yaml", "endpoint:\n  base_url: x\nmodels:\n  - id: m\n")


def _pack(tmp_path: Path) -> Path:
    return _write(tmp_path / "p.yaml", "id: pk\n")


# ---------------------------------------------------------------- Task 1: schema
def test_load_queue_minimal(tmp_path: Path):
    cfg, pack = _cfg(tmp_path), _pack(tmp_path)
    q = _write(
        tmp_path / "q.yaml",
        f'entries:\n  - config: {cfg}\n    pack: {pack}\n    model: {{ id: "a/b" }}\n',
    )
    spec = rq.load_queue(q)
    assert len(spec.entries) == 1
    assert spec.entries[0].model.id == "a/b"
    assert spec.entries[0].judge_config is None
    assert spec.defaults.reset_command == "lms unload --all"
    assert spec.defaults.step_timeout_s.judge_s == 21600


def test_load_queue_string_model_shorthand(tmp_path: Path):
    cfg, pack = _cfg(tmp_path), _pack(tmp_path)
    q = _write(
        tmp_path / "q.yaml",
        f'entries:\n  - config: {cfg}\n    pack: {pack}\n    model: "only/id"\n',
    )
    spec = rq.load_queue(q)
    assert spec.entries[0].model.id == "only/id"


def test_load_queue_missing_config_raises(tmp_path: Path):
    pack = _pack(tmp_path)
    q = _write(
        tmp_path / "q.yaml",
        f"entries:\n  - config: {tmp_path / 'nope.yaml'}\n    pack: {pack}\n    model: m\n",
    )
    with pytest.raises(ValueError, match="config"):
        rq.load_queue(q)


def test_load_queue_defaults_override_per_entry(tmp_path: Path):
    cfg, pack = _cfg(tmp_path), _pack(tmp_path)
    q = _write(
        tmp_path / "q.yaml",
        f"""
defaults:
  cooldown_s: 5
  step_timeout_s: {{ eval: 10, judge: 20 }}
entries:
  - config: {cfg}
    pack: {pack}
    model: m
    cooldown_s: 99
""",
    )
    spec = rq.load_queue(q)
    assert spec.defaults.cooldown_s == 5
    assert spec.entries[0].cooldown_s == 99
    assert spec.defaults.step_timeout_s.eval_s == 10


def test_load_queue_no_entries_raises(tmp_path: Path):
    q = _write(tmp_path / "q.yaml", "entries: []\n")
    with pytest.raises(ValueError, match="no entries"):
        rq.load_queue(q)


def test_run_dir_for_sanitizes_slashes():
    assert (
        rq.run_dir_for("2026-06-27_2200", "qwen/qwen3.6-27b", "buero")
        == "2026-06-27_2200_qwen-qwen3.6-27b_eval_buero"
    )
    assert rq.run_dir_for("t", "a/b", "p") == rq.run_dir_for("t", "a/b", "p")
