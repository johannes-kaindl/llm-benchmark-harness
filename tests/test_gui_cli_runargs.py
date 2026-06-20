import types

from typer.testing import CliRunner

from ramcheck.cli import _eval_event_writers, app

runner = CliRunner()


def test_eval_run_dir_option_pins_exact_dir(tmp_path, monkeypatch):
    """--run-dir makes eval use exactly that dir (no timestamp suffix appended)."""
    captured = {}

    def fake_run_eval(cfg, pk, client, *, run_dir, resume=False, **cb):
        captured["run_dir"] = run_dir
        return []

    monkeypatch.setattr("ramcheck.cli.run_eval", fake_run_eval)
    monkeypatch.setattr("ramcheck.cli._finalize_eval_bundle", lambda *a, **k: None)
    monkeypatch.setattr("ramcheck.cli._make_client", lambda cfg: object())

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
    on_run_start, _, _, _run_done = _eval_event_writers(path, append=False)
    on_run_start(2)
    text = path.read_text(encoding="utf-8")
    assert "run_done" not in text  # old line gone
    assert '"run_start"' in text and '"total": 2' in text


def test_eval_emit_events_writes_events_without_monitor(tmp_path, monkeypatch):
    """--emit-events writes events.jsonl but never spawns _live_monitor."""
    spawned = {"monitor": False}
    monkeypatch.setattr(
        "ramcheck.cli._live_monitor",
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
    ):
        if on_run_start:
            on_run_start(1)
        return []

    monkeypatch.setattr("ramcheck.cli.run_eval", fake_run_eval)
    monkeypatch.setattr("ramcheck.cli._finalize_eval_bundle", lambda *a, **k: None)
    monkeypatch.setattr("ramcheck.cli._make_client", lambda cfg: object())

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
        "ramcheck.cli._live_monitor",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no monitor")),
    )
    # minimal bundle
    b = tmp_path / "bundle"
    b.mkdir()
    (b / "bundle.json").write_text(
        '{"pack_path":"packs/ndassist.yaml","host":{}}', encoding="utf-8"
    )
    (b / "responses.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr("ramcheck.cli.load_responses_jsonl", lambda p: [])
    monkeypatch.setattr("ramcheck.cli._judge_and_persist", lambda *a, **k: ([], []))
    monkeypatch.setattr("ramcheck.cli._render_judge_scorecard", lambda *a, **k: None)
    monkeypatch.setattr("ramcheck.cli.OpenAIJudgeBackend", lambda *a, **k: object())
    monkeypatch.setattr(
        "ramcheck.cli.load_judge_config",
        lambda p: types.SimpleNamespace(
            endpoint=types.SimpleNamespace(base_url="x", api_key="y"),
            model="m",
            temperature=0.0,
        ),
    )
    res = runner.invoke(
        app,
        ["judge", "--bundle", str(b), "--judge-config", "judge.yaml", "--emit-events"],
    )
    assert res.exit_code == 0, res.output
    assert (b / "judge_events.jsonl").exists()
