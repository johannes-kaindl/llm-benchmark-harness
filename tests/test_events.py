from touchstone import events as ev


def test_constructors_have_type_tags():
    assert ev.run_start_event(1.0, 5)["type"] == "run_start"
    assert ev.run_start_event(1.0, 5)["total"] == 5
    cs = ev.cell_start_event(2.0, 0, "m", "v", "A", "p", 0)
    assert cs["type"] == "cell_start" and cs["model"] == "m" and cs["prompt_id"] == "p"
    cd = ev.cell_done_event(3.0, 0, "m", "v", "p", 0, True, 0.3, 1.2, 5.0, 7, False, "")
    assert cd["type"] == "cell_done" and cd["ok"] is True and cd["e2e_s"] == 1.2
    assert ev.run_done_event(4.0, 5, 4)["type"] == "run_done"


def test_dumps_roundtrips_through_parse_line():
    line = ev.dumps(ev.run_start_event(1.0, 3))
    assert ev.parse_line(line) == {"ts": 1.0, "type": "run_start", "total": 3}


def test_parse_line_tolerates_garbage():
    assert ev.parse_line("") is None
    assert ev.parse_line("   ") is None
    assert ev.parse_line('{"truncated": ') is None  # half-written line
    assert ev.parse_line("[1,2,3]") is None  # not a dict
    assert ev.parse_line('{"no":"type"}') is None  # missing type tag


def test_build_view_empty():
    v = ev.build_view([])
    assert v.total == 0 and v.done == 0 and v.eta_s is None and v.finished is False


def test_build_view_progress_ok_fail_and_running():
    events = [
        ev.run_start_event(0.0, 4),
        ev.cell_start_event(0.1, 0, "m", "v", "A", "p1", 0),
        ev.cell_done_event(0.2, 0, "m", "v", "p1", 0, True, 0.3, 10.0, 5.0, 7, False, ""),
        ev.cell_done_event(0.3, 1, "m", "v", "p2", 0, False, 0.0, 10.0, 0.0, 0, False, "boom"),
        ev.cell_start_event(0.4, 2, "m", "v", "A", "p3", 0),  # still running
    ]
    v = ev.build_view(events)
    assert v.total == 4
    assert v.done == 2 and v.ok == 1 and v.failed == 1
    assert len(v.running) == 1 and v.running[0].prompt_id == "p3"
    # ETA = mean(e2e of done) * remaining = ((10+10)/2) * (4-2) = 20
    assert v.eta_s == 20.0


def test_build_view_dedups_by_cell_key_for_resume():
    # a cell that ran twice across a crash+resume must count once (last wins)
    key_events = [
        ev.run_start_event(0.0, 1),
        ev.cell_start_event(0.1, 0, "m", "v", "A", "p1", 0),
        ev.cell_done_event(0.2, 0, "m", "v", "p1", 0, False, 0.3, 9.0, 5.0, 7, False, "x"),
        ev.run_start_event(1.0, 1),  # resume writes a fresh run_start
        ev.cell_done_event(1.2, 0, "m", "v", "p1", 0, True, 0.3, 9.0, 5.0, 7, False, ""),
    ]
    v = ev.build_view(key_events)
    assert v.done == 1 and v.ok == 1 and v.failed == 0  # not 2


def test_build_view_finished_on_run_done():
    v = ev.build_view([ev.run_start_event(0.0, 1), ev.run_done_event(1.0, 1, 1)])
    assert v.finished is True


def test_run_view_as_dict_is_json_safe():
    import json

    v = ev.build_view(
        [
            ev.run_start_event(0.0, 1),
            ev.cell_done_event(0.2, 0, "m", "v", "p1", 0, True, 0.3, 9.0, 5.0, 7, False, ""),
        ]
    )
    json.dumps(v.as_dict())  # must not raise
    assert v.as_dict()["cells"][0]["key"] == ["m", "v", "p1", 0]


def test_build_view_folds_preflight():
    from touchstone.events import build_view, preflight_event

    ev_pf = preflight_event(
        1.0,
        [
            {
                "model": "gemma",
                "status": "reasoning_only",
                "text_chars": 0,
                "reasoning_chars": 1400,
                "detail": "nur Reasoning",
            },
            {"model": "qwen", "status": "ok", "text_chars": 12, "reasoning_chars": 0, "detail": ""},
        ],
    )
    view = build_view([ev_pf])
    assert view.as_dict()["preflight"] == ev_pf["results"]
