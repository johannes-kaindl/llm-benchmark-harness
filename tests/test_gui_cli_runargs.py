import types

from typer.testing import CliRunner

from touchstone.cli import _eval_event_writers, app
from touchstone.gui import control

runner = CliRunner()


def _judge_mocks(monkeypatch):
    monkeypatch.setattr("touchstone.cli.load_responses_jsonl", lambda p: [])
    monkeypatch.setattr("touchstone.cli._render_judge_scorecard", lambda *a, **k: None)
    monkeypatch.setattr("touchstone.cli._write_result_json_after_judge", lambda *a, **k: None)
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
            suppress_thinking=False,
        ),
    )


def _judge_bundle(tmp_path):
    b = tmp_path / "bundle"
    (b / "run.json").parent.mkdir(parents=True, exist_ok=True)
    (b / "bundle.json").write_text(
        '{"pack_path":"packs/ndassist.yaml","host":{}}', encoding="utf-8"
    )
    (b / "responses.jsonl").write_text("", encoding="utf-8")
    return b


def test_eval_finalizes_sentinel_finished_on_success(tmp_path, monkeypatch):
    """A GUI-spawned eval writes its own terminal sentinel state so the overview
    no longer depends on reaping the (zombie) child to leave 'running'."""
    monkeypatch.setattr("touchstone.cli.run_eval", lambda *a, **k: [])
    monkeypatch.setattr("touchstone.cli._finalize_eval_bundle", lambda *a, **k: None)
    monkeypatch.setattr("touchstone.cli._make_client", lambda cfg: object())
    target = tmp_path / "run1"
    control.write_sentinel(target, kind="eval", pid=99, pack_path="p", config_path="c")
    res = runner.invoke(
        app,
        [
            "eval",
            "--pack",
            "packs/ndassist.yaml",
            "--config",
            "config.example.yaml",
            "--run-dir",
            str(target),
            "--emit-events",
        ],
    )
    assert res.exit_code == 0, res.output
    assert control.read_sentinel(target)["state"] == "finished"


def test_eval_finalizes_sentinel_failed_on_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr("touchstone.cli.run_eval", boom)
    monkeypatch.setattr("touchstone.cli._make_client", lambda cfg: object())
    target = tmp_path / "run2"
    control.write_sentinel(target, kind="eval", pid=99, pack_path="p", config_path="c")
    res = runner.invoke(
        app,
        [
            "eval",
            "--pack",
            "packs/ndassist.yaml",
            "--config",
            "config.example.yaml",
            "--run-dir",
            str(target),
            "--emit-events",
        ],
    )
    assert res.exit_code != 0
    assert control.read_sentinel(target)["state"] == "failed"


def test_judge_finalizes_sentinel_finished_on_success(tmp_path, monkeypatch):
    _judge_mocks(monkeypatch)
    monkeypatch.setattr("touchstone.cli._judge_and_persist", lambda *a, **k: ([], []))
    b = _judge_bundle(tmp_path)
    control.write_sentinel(b, kind="judge", pid=99, pack_path="", config_path="c")
    res = runner.invoke(
        app, ["judge", "--bundle", str(b), "--judge-config", "judge.yaml", "--emit-events"]
    )
    assert res.exit_code == 0, res.output
    assert control.read_sentinel(b)["state"] == "finished"


def test_judge_finalizes_sentinel_failed_on_error(tmp_path, monkeypatch):
    """The reported bug: a judge process died mid-run and the card stayed 'Judge läuft'."""
    _judge_mocks(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("judge process died")

    monkeypatch.setattr("touchstone.cli._judge_and_persist", boom)
    b = _judge_bundle(tmp_path)
    control.write_sentinel(b, kind="judge", pid=99, pack_path="", config_path="c")
    res = runner.invoke(
        app, ["judge", "--bundle", str(b), "--judge-config", "judge.yaml", "--emit-events"]
    )
    assert res.exit_code != 0
    assert control.read_sentinel(b)["state"] == "failed"


def test_eval_run_dir_option_pins_exact_dir(tmp_path, monkeypatch):
    """--run-dir makes eval use exactly that dir (no timestamp suffix appended)."""
    captured = {}

    def fake_run_eval(cfg, pk, client, *, run_dir, resume=False, **cb):
        captured["run_dir"] = run_dir
        return []

    monkeypatch.setattr("touchstone.cli.run_eval", fake_run_eval)
    monkeypatch.setattr("touchstone.cli._finalize_eval_bundle", lambda *a, **k: None)
    monkeypatch.setattr("touchstone.cli._make_client", lambda cfg: object())

    target = tmp_path / "my_exact_run"
    res = runner.invoke(
        app,
        [
            "eval",
            "--pack",
            "packs/ndassist.yaml",
            "--config",
            "config.example.yaml",
            "--run-dir",
            str(target),
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["run_dir"] == target


def test_eval_event_writers_truncate_mode_overwrites(tmp_path):
    """append=False truncates so each spawn starts a fresh stream (no stale run_done)."""
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"run_done","ts":1,"total":9,"ok":9}\n', encoding="utf-8")
    on_run_start, _, _, _, _run_done = _eval_event_writers(path, append=False)
    on_run_start(2)
    text = path.read_text(encoding="utf-8")
    assert "run_done" not in text  # old line gone
    assert '"run_start"' in text and '"total": 2' in text


def test_eval_emit_events_writes_events_without_monitor(tmp_path, monkeypatch):
    """--emit-events writes events.jsonl but never spawns _live_monitor."""
    spawned = {"monitor": False}
    monkeypatch.setattr(
        "touchstone.cli._live_monitor",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("monitor must not spawn")),
    )

    def fake_run_eval(
        cfg,
        pk,
        client,
        *,
        run_dir,
        resume=False,
        on_run_start=None,
        on_cell_start=None,
        on_cell_done=None,
        on_preflight=None,
        strict_preflight=False,
    ):
        if on_run_start:
            on_run_start(1)
        return []

    monkeypatch.setattr("touchstone.cli.run_eval", fake_run_eval)
    monkeypatch.setattr("touchstone.cli._finalize_eval_bundle", lambda *a, **k: None)
    monkeypatch.setattr("touchstone.cli._make_client", lambda cfg: object())

    target = tmp_path / "run1"
    res = runner.invoke(
        app,
        [
            "eval",
            "--pack",
            "packs/ndassist.yaml",
            "--config",
            "config.example.yaml",
            "--run-dir",
            str(target),
            "--emit-events",
        ],
    )
    assert res.exit_code == 0, res.output
    assert (target / "events.jsonl").exists()
    assert not spawned["monitor"]


def test_judge_emit_events_no_monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "touchstone.cli._live_monitor",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no monitor")),
    )
    # minimal bundle
    b = tmp_path / "bundle"
    b.mkdir()
    (b / "bundle.json").write_text(
        '{"pack_path":"packs/ndassist.yaml","host":{}}', encoding="utf-8"
    )
    (b / "responses.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr("touchstone.cli.load_responses_jsonl", lambda p: [])
    monkeypatch.setattr("touchstone.cli._judge_and_persist", lambda *a, **k: ([], []))
    monkeypatch.setattr("touchstone.cli._render_judge_scorecard", lambda *a, **k: None)
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
            suppress_thinking=False,
        ),
    )
    res = runner.invoke(
        app,
        ["judge", "--bundle", str(b), "--judge-config", "judge.yaml", "--emit-events"],
    )
    assert res.exit_code == 0, res.output
    assert (b / "judge_events.jsonl").exists()
