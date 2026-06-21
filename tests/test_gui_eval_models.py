# tests/test_gui_eval_models.py
from __future__ import annotations

import json

from ramcheck.config import ModelSpec
from ramcheck.gui import control


class _Rec:
    def __init__(self):
        self.calls = []

    def spawn(self, argv):
        self.calls.append(argv)
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def test_start_eval_adds_models_json_when_given(tmp_path):
    rec = _Rec()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=rec)
    reg.start_eval(
        pack_path="packs/ndassist.yaml",
        config_path="config.m5.yaml",
        models=[ModelSpec(id="a", quant="Q4"), ModelSpec(id="b")],
    )
    argv = rec.calls[0]
    assert "--models-json" in argv
    payload = json.loads(argv[argv.index("--models-json") + 1])
    assert [m["id"] for m in payload] == ["a", "b"]
    assert payload[0]["quant"] == "Q4"


def test_start_eval_omits_models_json_when_none(tmp_path):
    rec = _Rec()
    reg = control.RunRegistry(runs_dir=tmp_path, launcher=rec)
    reg.start_eval(pack_path="p", config_path="c")
    assert "--models-json" not in rec.calls[0]
