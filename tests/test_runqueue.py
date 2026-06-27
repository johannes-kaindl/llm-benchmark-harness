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


def test_load_queue_rejects_duplicate_entries(tmp_path: Path):
    # same (config, pack, model) twice would map to the SAME bundle dir -> silent overwrite
    cfg, pack = _cfg(tmp_path), _pack(tmp_path)
    q = _write(
        tmp_path / "q.yaml",
        f"entries:\n"
        f"  - config: {cfg}\n    pack: {pack}\n    model: dup\n"
        f"  - config: {cfg}\n    pack: {pack}\n    model: dup\n",
    )
    with pytest.raises(ValueError, match="duplicate"):
        rq.load_queue(q)


def test_run_dir_for_sanitizes_slashes():
    assert (
        rq.run_dir_for("2026-06-27_2200", "qwen/qwen3.6-27b", "buero", 0)
        == "2026-06-27_2200_e0_qwen-qwen3.6-27b_eval_buero"
    )
    assert rq.run_dir_for("t", "a/b", "p", 1) == rq.run_dir_for("t", "a/b", "p", 1)


def test_run_dir_for_index_disambiguates_slug_collisions():
    # distinct model ids that slugify equal must NOT collide (would silently overwrite a bundle)
    a = rq.run_dir_for("t", "m:q4", "p", 0)
    b = rq.run_dir_for("t", "m-q4", "p", 1)
    assert a != b


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


# --------------------------------------- Task 7: real reset / step / prior-results
def test_run_reset_handles_missing_binary():
    assert rq.run_reset("definitely-not-a-real-binary-xyz --flags") is False


def test_run_reset_empty_is_noop():
    assert rq.run_reset("") is True


def test_run_reset_ok():
    class R:
        returncode = 0

    assert rq.run_reset("whatever args", runner=lambda *a, **k: R()) is True


def test_run_reset_nonzero_is_false():
    class R:
        returncode = 3

    assert rq.run_reset("whatever", runner=lambda *a, **k: R()) is False


def test_make_run_step_timeout_kills(tmp_path: Path):
    import subprocess

    created: list = []

    class FakePopen:
        def __init__(self, argv, **k):
            self.argv = argv
            self.returncode = None
            self.killed = False
            self.terminated = False
            created.append(self)

        def wait(self, timeout=None):
            # SIGTERM is ignored — only kill() lets wait() return
            if not self.killed:
                raise subprocess.TimeoutExpired(self.argv, timeout)
            self.returncode = -9
            return -9

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    step = rq.make_run_step(clock=lambda: 0.0, popen=FakePopen, grace_s=0)
    out = step("eval", ["x"], 1.0, tmp_path)
    assert out.status == "timeout"
    # the named SIGKILL escalation must actually be reached, not just status=timeout
    assert created[0].terminated and created[0].killed


def test_make_run_step_unkillable_does_not_hang(tmp_path: Path):
    # a process stuck in D-state never reaps even after SIGKILL; the post-kill wait must be
    # bounded so one wedged step can't sink the whole overnight queue.
    import subprocess

    class StuckPopen:
        def __init__(self, argv, **k):
            self.argv = argv
            self.returncode = None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(self.argv, timeout)  # never returns

        def terminate(self):
            pass

        def kill(self):
            pass

    step = rq.make_run_step(clock=lambda: 0.0, popen=StuckPopen, grace_s=0)
    out = step("eval", ["x"], 1.0, tmp_path)  # must return, not hang
    assert out.status == "timeout"


def test_make_run_step_spawn_failure_is_failed(tmp_path: Path):
    # an OSError at spawn time (bad python, ENOMEM, EMFILE) must NOT crash the queue
    class BoomPopen:
        def __init__(self, argv, **k):
            raise OSError("cannot spawn")

    step = rq.make_run_step(clock=lambda: 0.0, popen=BoomPopen)
    assert step("eval", ["x"], 10.0, tmp_path).status == "failed"


def test_make_run_step_ok_when_artifacts_present(tmp_path: Path):
    (tmp_path / "responses.jsonl").write_text("{}\n", encoding="utf-8")

    class FakePopen:
        def __init__(self, argv, **k):
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

    step = rq.make_run_step(clock=lambda: 0.0, popen=FakePopen)
    assert step("eval", ["x"], 10.0, tmp_path).status == "ok"


def test_load_prior_results_roundtrip(tmp_path: Path):
    qd = tmp_path / "q"
    res = [rq.EntryResult(0, "a/b", "c", "p", "runs/x", "ok", "ok", 1.0, 2.0)]
    rq._write_summary(qd, rq.QueueSpec(entries=[]), res, "ISO")
    back = rq.load_prior_results(qd)
    assert back[0].model_id == "a/b"
    assert back[0].eval_status == "ok"
    assert back[0].eval_seconds == 1.0


def test_load_prior_results_missing_is_empty(tmp_path: Path):
    assert rq.load_prior_results(tmp_path / "nope") == []


# ----------------------------------------------- Task 8: --check verify mode
def test_distinct_models_dedupe(tmp_path: Path):
    cfg, pack = _cfg(tmp_path), _pack(tmp_path)

    def mk(mid: str) -> rq.QueueEntry:
        return rq.QueueEntry(config=cfg, pack=pack, model={"id": mid})

    spec = rq.QueueSpec(entries=[mk("a"), mk("a"), mk("b")])
    assert [m.id for m in rq.distinct_models(spec)] == ["a", "b"]


def test_run_check_probes_each_distinct_model(tmp_path: Path):
    spec = _spec_two(tmp_path, judge=False)  # models a/b, c/d
    resets: list[str] = []
    emitted: list[str] = []

    def probe(entry, model):
        return (True, "ok", 42)

    rows = rq.run_check(
        spec,
        reset_run=lambda c: resets.append(c) or True,
        ram_poll=lambda: 1000.0,
        sleep=lambda s: None,
        clock=lambda: 0.0,
        probe=probe,
        emit=emitted.append,
    )
    assert [r["model"] for r in rows] == ["a/b", "c/d"]
    assert all(r["loaded"] for r in rows)
    assert len(resets) == 2  # reset before probing each model
    assert len(emitted) == 2


def test_shipped_queue_example_validates():
    spec = rq.load_queue("queue.example.yaml")
    assert len(spec.entries) >= 1


# ------------------------------------------------- review fixes: robustness gaps
def test_load_prior_results_corrupt_returns_empty(tmp_path: Path):
    qd = tmp_path / "q"
    qd.mkdir()
    (qd / "summary.json").write_text('{"entries": [{"index": 0, "model_id": "foo', encoding="utf-8")
    assert rq.load_prior_results(qd) == []  # half-written file -> [] (resume degrades, no crash)


def test_build_eval_argv_resume_uses_resume_flag(tmp_path: Path):
    e = _entry(tmp_path)
    argv = rq.build_eval_argv(e, tmp_path / "bundle", python="PY", resume=True)
    assert "--resume" in argv
    assert "--run-dir" not in argv  # --resume targets the bundle dir on its own
    assert argv[argv.index("--resume") + 1] == str(tmp_path / "bundle")


def test_run_queue_resume_retry_replaces_row(tmp_path: Path):
    # a prior failed entry that now succeeds must REPLACE its row, not append a duplicate
    spec = _spec_two(tmp_path, judge=False)
    prior = [
        rq.EntryResult(0, "a/b", "c", "p", "runs/x", "failed", "skipped", error="eval failed"),
        rq.EntryResult(1, "c/d", "c", "p", "runs/y", "ok", "skipped"),
    ]
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
        prior=prior,
    )
    idx0 = [r for r in res if r.index == 0]
    assert len(idx0) == 1  # exactly one row for index 0
    assert idx0[0].eval_status == "ok"  # the retried, now-successful one
    assert len(res) == 2  # done == total, no duplicate


def test_run_queue_writes_summary_incrementally(tmp_path: Path):
    # summary.json must contain entry 0 BEFORE entry 1 starts (crash robustness / resume basis)
    import json

    spec = _spec_two(tmp_path, judge=False)
    qd = tmp_path / "q"
    seen_counts: list[int] = []

    def run_step(kind, argv, timeout, bundle):
        f = qd / "summary.json"
        seen_counts.append(len(json.loads(f.read_text())["entries"]) if f.exists() else 0)
        return rq.StepOutcome("ok", 1.0)

    rq.run_queue(
        spec,
        queue_dir=qd,
        output_dir=tmp_path / "runs",
        python="PY",
        run_step=run_step,
        reset_run=lambda c: True,
        ram_poll=lambda: 1.0,
        sleep=lambda s: None,
        clock=lambda: 0.0,
        ts="T",
        started_iso="I",
    )
    assert seen_counts == [0, 1]  # entry 0's eval sees 0 on disk; entry 1's eval sees 1


def test_run_queue_judge_failure_recorded(tmp_path: Path):
    spec = _spec_two(tmp_path)  # judge=True

    def run_step(kind, argv, timeout, bundle):
        return rq.StepOutcome("ok" if kind == "eval" else "failed", 1.0)

    res = rq.run_queue(
        spec,
        queue_dir=tmp_path / "q",
        output_dir=tmp_path / "runs",
        python="PY",
        run_step=run_step,
        reset_run=lambda c: True,
        ram_poll=lambda: 1.0,
        sleep=lambda s: None,
        clock=lambda: 0.0,
        ts="T",
        started_iso="I",
    )
    assert all(r.eval_status == "ok" for r in res)
    assert all(r.judge_status == "failed" for r in res)
    assert all(r.error.startswith("judge") for r in res)
    # a judge-failed entry is NOT complete -> a later resume re-runs it
    assert [i for i, _ in rq.entries_to_run(spec, res)] == [0, 1]


def test_run_queue_settle_timeout_warns(tmp_path: Path):
    # spec: on settle timeout -> warn + continue (don't swallow it silently)
    spec = _spec_two(tmp_path, judge=False)
    spec.defaults.settle = rq.SettleSpec(timeout_s=1, plateau_polls=99, poll_interval_s=1)
    warned: list[str] = []
    ticking = {"t": 0.0}

    def clock() -> float:
        return ticking["t"]

    def sleep(s: float) -> None:
        ticking["t"] += s  # drive past settle.timeout_s so settle never plateaus

    rq.run_queue(
        spec,
        queue_dir=tmp_path / "q",
        output_dir=tmp_path / "runs",
        python="PY",
        run_step=lambda *a: rq.StepOutcome("ok", 1.0),
        reset_run=lambda c: True,
        ram_poll=lambda: 9999.0,
        sleep=sleep,
        clock=clock,
        ts="T",
        started_iso="I",
        log=warned.append,
    )
    assert any("settle" in w.lower() for w in warned)


def test_run_check_writes_check_md(tmp_path: Path):
    spec = _spec_two(tmp_path, judge=False)
    check_dir = tmp_path / "check"
    rq.run_check(
        spec,
        reset_run=lambda c: True,
        ram_poll=lambda: 1000.0,
        sleep=lambda s: None,
        clock=lambda: 0.0,
        probe=lambda entry, model: (True, "ok", 42),
        emit=lambda s: None,
        check_dir=check_dir,
    )
    md = (check_dir / "check.md").read_text(encoding="utf-8")
    assert "a/b" in md and "c/d" in md
