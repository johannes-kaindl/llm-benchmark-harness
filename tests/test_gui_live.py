# tests/test_gui_live.py
from ramcheck import events as ev
from ramcheck.gui import live


def test_eval_stream_folds_events(tmp_path):
    p = tmp_path / "events.jsonl"
    lines = [
        ev.dumps(ev.run_start_event(1.0, 2)),
        ev.dumps(ev.cell_start_event(1.1, 0, "m", "v", "A", "A1", 0)),
        ev.dumps(ev.cell_done_event(1.2, 0, "m", "v", "A1", 0, True, 0.3, 1.0, 5.0, 7, False, "")),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stream = live.LiveStream(p, kind="eval")
    view = stream.snapshot()
    assert view["total"] == 2 and view["done"] == 1
    assert view["finished"] is False


def test_stream_tolerates_missing_file(tmp_path):
    stream = live.LiveStream(tmp_path / "nope.jsonl", kind="eval")
    view = stream.snapshot()
    assert view["total"] == 0 and view["done"] == 0


def test_judge_stream_uses_judge_view(tmp_path):
    from ramcheck import judge_events as je

    p = tmp_path / "judge_events.jsonl"
    p.write_text(je.dumps(je.judge_start_event(1.0, 3)) + "\n", encoding="utf-8")
    stream = live.LiveStream(p, kind="judge")
    assert stream.snapshot()["total"] == 3
