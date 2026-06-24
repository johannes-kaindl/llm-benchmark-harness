# tests/test_gui_live.py
from touchstone import events as ev
from touchstone.gui import live


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
    from touchstone import judge_events as je

    p = tmp_path / "judge_events.jsonl"
    p.write_text(je.dumps(je.judge_start_event(1.0, 3)) + "\n", encoding="utf-8")
    stream = live.LiveStream(p, kind="judge")
    assert stream.snapshot()["total"] == 3


def test_truncation_clears_stale_events(tmp_path):
    """A fresh judge --web run rewrites judge_events.jsonl mid-stream (truncation).

    The stale events must be dropped so the view reflects only the new stream,
    not a mix that keeps total stuck at the larger stale value (MAJOR 3).
    """
    from touchstone import judge_events as je

    p = tmp_path / "judge_events.jsonl"
    # First stream: total=5 plus four verdicts.
    first = [je.dumps(je.judge_start_event(1.0, 5))]
    for i in range(4):
        first.append(
            je.dumps(
                je.verdict_event(1.0 + i, i, "m", "baseline", "A1", i, "A", 5, False, False, "")
            )
        )
    p.write_text("\n".join(first) + "\n", encoding="utf-8")
    stream = live.LiveStream(p, kind="judge")
    v1 = stream.snapshot()
    assert v1["total"] == 5

    # Truncate + rewrite with a smaller stream: total=2 plus two verdicts.
    second = [je.dumps(je.judge_start_event(10.0, 2))]
    for i in range(2):
        second.append(
            je.dumps(
                je.verdict_event(10.0 + i, i, "m", "baseline", "B1", i, "B", 4, False, False, "")
            )
        )
    p.write_text("\n".join(second) + "\n", encoding="utf-8")
    v2 = stream.snapshot()
    # Must reflect only the 2 new events — not 5, and not a 5+2 mix.
    assert v2["total"] == 2
    assert v2["done"] == 2
