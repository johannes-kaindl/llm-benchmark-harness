from typer.testing import CliRunner

from touchstone.cli import app

runner = CliRunner()


def test_gui_command_without_extra_prints_install_hint(monkeypatch):
    """If FastAPI isn't importable, `touchstone gui` exits 1 with an install hint, no traceback."""
    import builtins

    real_import = builtins.__import__

    def block_gui(name, *a, **k):
        if name.startswith("touchstone.gui.app"):
            raise ImportError("No module named 'fastapi'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", block_gui)
    res = runner.invoke(app, ["gui"])
    assert res.exit_code == 1
    assert "pip install" in res.output and "[gui]" in res.output
