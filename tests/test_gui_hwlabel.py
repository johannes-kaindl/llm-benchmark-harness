from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.gui.hwlabel import label_mismatch


class _FakeLauncher:
    def spawn(self, argv):
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _write_scores_bundle(d, *, chip, ram_gb, machine):
    """A minimal judged bundle whose scores.csv carries hardware columns + one
    dimension row, so the /compare aggregate path produces a real AggRow."""
    import json

    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-20",
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text("", encoding="utf-8")
    header = [
        "machine",
        "chip",
        "ram_gb",
        "pack",
        "pack_version",
        "model",
        "quant",
        "variant",
        "metric_type",
        "metric",
        "weight",
        "score",
    ]
    row = [
        machine,
        chip,
        ram_gb,
        "ndassist",
        "1",
        "m",
        "q",
        "baseline",
        "dimension",
        "Q1",
        "2",
        "4",
    ]
    (d / "scores.csv").write_text(",".join(header) + "\n" + ",".join(row) + "\n", encoding="utf-8")


def test_compare_route_flags_stale_machine_label(tmp_path):
    # detected M5 / 64GB but the config label still says M1-16GB -> demoted with ⚠
    _write_scores_bundle(
        tmp_path / "2026_eval_nd", chip="Apple M5 Pro", ram_gb="64.0", machine="M1-16GB"
    )
    r = _client(tmp_path).get("/compare")
    assert r.status_code == 200
    assert "⚠" in r.text


def test_label_matches_detected_hardware_is_not_flagged():
    assert label_mismatch(chip="Apple M5 Pro", ram_gb="64.0", machine="M5-Pro-64GB") is False


def test_stale_label_contradicting_detected_chip_is_flagged():
    # label says M1/16GB but the run actually executed on an M5 with 64GB
    assert label_mismatch(chip="Apple M5 Pro", ram_gb="64.0", machine="M1-16GB") is True


def test_empty_or_unknown_label_is_not_flagged():
    assert label_mismatch(chip="Apple M5 Pro", ram_gb="64.0", machine="") is False
    assert label_mismatch(chip="", ram_gb="", machine="whatever") is False
