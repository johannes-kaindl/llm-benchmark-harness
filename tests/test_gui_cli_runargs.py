from typer.testing import CliRunner

from ramcheck.cli import app

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
