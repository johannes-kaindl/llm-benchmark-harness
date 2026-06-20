from types import SimpleNamespace

from ramcheck import events as ev
from ramcheck.cli import _eval_event_writers, _run_event_writers


def _cell():
    return SimpleNamespace(
        model=SimpleNamespace(id="m"),
        variant=SimpleNamespace(id="v"),
        category=SimpleNamespace(id="A"),
        prompt=SimpleNamespace(id="p"),
        repeat=0,
    )


def _resp():
    return SimpleNamespace(
        model="m",
        variant="v",
        prompt_id="p",
        repeat=0,
        ok=True,
        ttft_s=0.3,
        e2e_s=1.2,
        decode_tps=5.0,
        completion_tokens=7,
        content_empty=False,
        error="",
    )


def test_eval_event_writers_emit_well_formed_events(tmp_path):
    path = tmp_path / "events.jsonl"
    on_run_start, on_cell_start, on_cell_done, run_done = _eval_event_writers(path)
    on_run_start(3)
    on_cell_start(0, _cell())
    on_cell_done(0, _resp())
    run_done([_resp(), _resp()])

    parsed = [ev.parse_line(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    parsed = [p for p in parsed if p is not None]
    assert [p["type"] for p in parsed] == ["run_start", "cell_start", "cell_done", "run_done"]
    assert parsed[0]["total"] == 3
    assert parsed[1]["model"] == "m" and parsed[1]["prompt_id"] == "p"
    assert parsed[2]["ok"] is True and parsed[2]["e2e_s"] == 1.2
    assert parsed[3]["ok"] == 2  # both responses ok


def _run_cell():
    return SimpleNamespace(model=SimpleNamespace(id="m"), scenario="chat", target_ctx=0)


def _run_rec(**kw):
    base = dict(
        model="m",
        scenario="chat",
        target_ctx=0,
        ok=True,
        ttft_s=0.2,
        e2e_s=1.0,
        decode_tps=50.0,
        completion_tokens=10,
        error="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_run_event_writers_emit_well_formed_events(tmp_path):
    path = tmp_path / "events.jsonl"
    on_run_start, on_cell_start, on_cell_done, run_done = _run_event_writers(path)
    on_run_start(2)
    on_cell_start(0, _run_cell())
    on_cell_done(0, _run_rec())
    run_done([_run_rec(), _run_rec(ok=False)])

    parsed = [ev.parse_line(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    parsed = [p for p in parsed if p is not None]
    assert [p["type"] for p in parsed] == ["run_start", "cell_start", "cell_done", "run_done"]
    assert parsed[1]["variant"] == "native" and parsed[1]["prompt_id"] == "chat"
    assert parsed[2]["ok"] is True and parsed[2]["decode_tps"] == 50.0
    assert parsed[3]["ok"] == 1  # one ok, one failed
