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


# ----------------------------------------------------------- Task 2: settle-wait
def _fake_time():
    t = {"now": 0.0}

    def clock() -> float:
        return t["now"]

    def sleep(s: float) -> None:
        t["now"] += s

    return clock, sleep, t


def test_settle_reaches_plateau():
    vals = iter([5000, 4000, 3000, 2980, 2975, 2972])  # drops then flattens
    clock, sleep, _ = _fake_time()
    out = rq.wait_until_settled(
        lambda: next(vals),
        sleep,
        clock,
        settle=rq.SettleSpec(timeout_s=100, plateau_polls=2, poll_interval_s=2, epsilon_mb=50),
    )
    assert out.settled is True
    # deltas 3000->2980 (20) and 2980->2975 (5) are the first two-in-a-row < epsilon -> settle
    # at 2975 (poll #5), without consuming the trailing 2972.
    assert out.final_mb == 2975
    assert out.polls == 5


def test_settle_times_out_when_never_stable():
    n = {"v": 9000.0}

    def ram() -> float:
        n["v"] -= 1000  # always drops > epsilon
        return n["v"]

    clock, sleep, _ = _fake_time()
    out = rq.wait_until_settled(
        ram,
        sleep,
        clock,
        settle=rq.SettleSpec(timeout_s=6, plateau_polls=3, poll_interval_s=2, epsilon_mb=50),
    )
    assert out.settled is False
    assert out.waited_s >= 6


# ----------------------------------------------- Task 3: argv builders + classify
def _entry(tmp_path: Path) -> rq.QueueEntry:
    return rq.QueueEntry(
        config=_cfg(tmp_path),
        pack=_pack(tmp_path),
        model={"id": "a/b"},
        judge_config=_write(tmp_path / "j.yaml", "model: jm\n"),
        judge_model="jm",
    )


def test_build_eval_argv(tmp_path: Path):
    import json

    e = _entry(tmp_path)
    argv = rq.build_eval_argv(e, tmp_path / "bundle", python="PY")
    assert argv[:4] == ["PY", "-m", "touchstone", "eval"]
    mj = argv[argv.index("--models-json") + 1]
    assert json.loads(mj) == [
        {
            "id": "a/b",
            "quant": "",
            "max_tokens_default": 400,
            "reasoning_headroom_tokens": 0,
            "extra_body": {},
        }
    ]
    assert "--emit-events" in argv
    assert argv[argv.index("--run-dir") + 1] == str(tmp_path / "bundle")


def test_build_judge_argv_with_model_override(tmp_path: Path):
    e = _entry(tmp_path)
    argv = rq.build_judge_argv(e, tmp_path / "bundle", python="PY")
    assert argv[:4] == ["PY", "-m", "touchstone", "judge"]
    assert argv[argv.index("--bundle") + 1] == str(tmp_path / "bundle")
    assert argv[argv.index("--judge-model") + 1] == "jm"


def test_build_judge_argv_without_model_override(tmp_path: Path):
    e = _entry(tmp_path)
    e.judge_model = ""
    argv = rq.build_judge_argv(e, tmp_path / "bundle", python="PY")
    assert "--judge-model" not in argv


def test_classify_step():
    assert rq.classify_step(None, True, False) == "timeout"
    assert rq.classify_step(1, False, True) == "failed"
    assert rq.classify_step(0, False, False) == "failed"  # exit 0 but artifacts missing
    assert rq.classify_step(0, False, True) == "ok"


# ----------------------------------------------------- Task 4: EntryResult + summary
def test_summary_json_and_md():
    res = [
        rq.EntryResult(0, "a/b", "c.yaml", "p.yaml", "runs/x", "ok", "ok", 12.0, 34.0),
        rq.EntryResult(
            1,
            "c/d",
            "c.yaml",
            "p.yaml",
            "runs/y",
            "timeout",
            "skipped",
            60.0,
            0.0,
            error="eval exceeded 14400s",
        ),
    ]
    spec = rq.QueueSpec(entries=[])
    obj = rq.summary_json_obj(spec, res, started_iso="2026-06-27T22:00:00")
    assert obj["started"] == "2026-06-27T22:00:00"
    assert obj["entries"][1]["eval_status"] == "timeout"
    assert obj["entries"][1]["judge_status"] == "skipped"
    md = rq.render_summary_md(spec, res, started_iso="2026-06-27T22:00:00")
    assert "a/b" in md and "c/d" in md
    assert "timeout" in md
    assert "runs/x" in md
    assert "eval exceeded 14400s" in md


# --------------------------------------------- Task 5: resolve_entry + entries_to_run
def test_resolve_entry_uses_overrides(tmp_path: Path):
    e = _entry(tmp_path)
    e.cooldown_s = 7
    e.reset_command = "custom"
    d = rq.QueueDefaults(cooldown_s=1, reset_command="def")
    r = rq.resolve_entry(e, d)
    assert r.cooldown_s == 7
    assert r.reset_command == "custom"
    assert r.eval_timeout_s == d.step_timeout_s.eval_s  # falls back to default


def test_resolve_entry_falls_back_to_defaults(tmp_path: Path):
    e = _entry(tmp_path)  # no per-entry overrides
    d = rq.QueueDefaults(cooldown_s=3, reset_command="def-cmd")
    r = rq.resolve_entry(e, d)
    assert r.cooldown_s == 3
    assert r.reset_command == "def-cmd"
    assert r.settle is d.settle


def test_entries_to_run_skips_completed(tmp_path: Path):
    spec = rq.QueueSpec(entries=[_entry(tmp_path), _entry(tmp_path)])
    prior = [rq.EntryResult(0, "a/b", "c", "p", "runs/x", "ok", "ok")]
    assert [i for i, _ in rq.entries_to_run(spec, prior)] == [1]


def test_entries_to_run_skips_eval_ok_judge_skipped(tmp_path: Path):
    spec = rq.QueueSpec(entries=[_entry(tmp_path)])
    prior = [rq.EntryResult(0, "a/b", "c", "p", "runs/x", "ok", "skipped")]
    assert rq.entries_to_run(spec, prior) == []


def test_entries_to_run_retries_failed(tmp_path: Path):
    spec = rq.QueueSpec(entries=[_entry(tmp_path)])
    prior = [rq.EntryResult(0, "a/b", "c", "p", "runs/x", "failed", "skipped")]
    assert [i for i, _ in rq.entries_to_run(spec, prior)] == [0]


# ------------------------------------------------ Task 6: run_queue orchestration
def _spec_two(tmp_path: Path, judge: bool = True) -> rq.QueueSpec:
    cfg, pack = _cfg(tmp_path), _pack(tmp_path)
    jc = _write(tmp_path / "j.yaml", "model: jm\n") if judge else None

    def mk(mid: str) -> rq.QueueEntry:
        return rq.QueueEntry(
            config=cfg, pack=pack, model={"id": mid}, judge_config=jc, judge_model=""
        )

    return rq.QueueSpec(entries=[mk("a/b"), mk("c/d")])


def _const_time():
    t = {"now": 0.0}
    return (lambda: t["now"]), (lambda s: t.__setitem__("now", t["now"] + s)), t


def test_run_queue_happy_path(tmp_path: Path):
    spec = _spec_two(tmp_path)
    calls: list[tuple[str, str]] = []
    resets: list[str] = []
    clock, sleep, _ = _const_time()
    qd = tmp_path / "q"

    def run_step(kind, argv, timeout, bundle):
        calls.append((kind, str(bundle)))
        return rq.StepOutcome("ok", 1.0)

    res = rq.run_queue(
        spec,
        queue_dir=qd,
        output_dir=tmp_path / "runs",
        python="PY",
        run_step=run_step,
        reset_run=lambda c: resets.append(c) or True,
        ram_poll=lambda: 1000.0,
        sleep=sleep,
        clock=clock,
        ts="T",
        started_iso="ISO",
    )
    assert [r.eval_status for r in res] == ["ok", "ok"]
    assert [r.judge_status for r in res] == ["ok", "ok"]
    assert [c[0] for c in calls] == ["eval", "judge", "eval", "judge"]
    assert len(resets) == 2  # reset before each entry
    assert (qd / "summary.json").exists() and (qd / "summary.md").exists()


def test_run_queue_eval_fail_skips_judge(tmp_path: Path):
    spec = _spec_two(tmp_path)
    clock, sleep, _ = _const_time()

    def run_step(kind, argv, timeout, bundle):
        return rq.StepOutcome("failed" if kind == "eval" else "ok", 1.0)

    res = rq.run_queue(
        spec,
        queue_dir=tmp_path / "q",
        output_dir=tmp_path / "runs",
        python="PY",
        run_step=run_step,
        reset_run=lambda c: True,
        ram_poll=lambda: 1.0,
        sleep=sleep,
        clock=clock,
        ts="T",
        started_iso="I",
    )
    assert all(r.eval_status == "failed" for r in res)
    assert all(r.judge_status == "skipped" for r in res)


def test_run_queue_timeout_continues(tmp_path: Path):
    spec = _spec_two(tmp_path)
    seen: list[str] = []
    clock, sleep, _ = _const_time()

    def run_step(kind, argv, timeout, bundle):
        seen.append(kind)
        return rq.StepOutcome("timeout" if kind == "eval" else "ok", float(timeout))

    res = rq.run_queue(
        spec,
        queue_dir=tmp_path / "q",
        output_dir=tmp_path / "runs",
        python="PY",
        run_step=run_step,
        reset_run=lambda c: True,
        ram_poll=lambda: 1.0,
        sleep=sleep,
        clock=clock,
        ts="T",
        started_iso="I",
    )
    assert [r.eval_status for r in res] == ["timeout", "timeout"]
    assert seen.count("eval") == 2  # second entry still attempted


def test_run_queue_no_judge_when_no_judge_config(tmp_path: Path):
    spec = _spec_two(tmp_path, judge=False)
    res = rq.run_queue(
        spec,
        queue_dir=tmp_path / "q",
        output_dir=tmp_path / "runs",
        python="PY",
        run_step=lambda *a: rq.StepOutcome("ok", 1.0),
        reset_run=lambda c: True,
        ram_poll=lambda: 1.0,
        sleep=lambda s: None,
        clock=lambda: 0.0,
        ts="T",
        started_iso="I",
    )
    assert all(r.judge_status == "skipped" for r in res)


def test_run_queue_reset_failure_continues(tmp_path: Path):
    spec = _spec_two(tmp_path, judge=False)
    warned: list[str] = []
    res = rq.run_queue(
        spec,
        queue_dir=tmp_path / "q",
        output_dir=tmp_path / "runs",
        python="PY",
        run_step=lambda *a: rq.StepOutcome("ok", 1.0),
        reset_run=lambda c: False,
        ram_poll=lambda: 1.0,
        sleep=lambda s: None,
        clock=lambda: 0.0,
        ts="T",
        started_iso="I",
        log=warned.append,
    )
    assert all(r.eval_status == "ok" for r in res)  # ran despite reset failing
    assert any("reset" in w.lower() for w in warned)


def test_run_queue_resumes_prior(tmp_path: Path):
    spec = _spec_two(tmp_path)
    prior = [rq.EntryResult(0, "a/b", "c", "p", "runs/x", "ok", "ok")]
    seen: list[str] = []
    rq.run_queue(
        spec,
        queue_dir=tmp_path / "q",
        output_dir=tmp_path / "runs",
        python="PY",
        run_step=lambda kind, *a: seen.append(kind) or rq.StepOutcome("ok", 1.0),
        reset_run=lambda c: True,
        ram_poll=lambda: 1.0,
        sleep=lambda s: None,
        clock=lambda: 0.0,
        ts="T",
        started_iso="I",
        prior=prior,
    )
    assert seen == ["eval", "judge"]  # only entry 1 ran
