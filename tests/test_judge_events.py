from ramcheck import judge_events as je


def test_constructors_have_type_tags():
    assert je.judge_start_event(1.0, 7)["type"] == "judge_start"
    assert je.judge_start_event(1.0, 7)["total"] == 7
    v = je.verdict_event(2.0, 0, "m", "v", "p1", 0, "A", 4, False, False, "gut")
    assert v["type"] == "verdict" and v["model"] == "m" and v["score"] == 4
    assert v["red_flag"] is False and v["unscored"] is False and v["rationale"] == "gut"
    ms = je.master_event(3.0, "m", "v", 72.5, True, "", "Ja")
    assert ms["type"] == "master" and ms["pct"] == 72.5 and ms["recommendation"] == "Ja"
    assert je.judge_done_event(4.0, 5, 4)["type"] == "judge_done"


def test_verdict_event_truncates_rationale():
    v = je.verdict_event(1.0, 0, "m", "v", "p", 0, "A", 3, False, False, "x" * 500)
    assert len(v["rationale"]) == 160


def test_dumps_roundtrips_through_parse_line():
    line = je.dumps(je.judge_start_event(1.0, 3))
    assert je.parse_line(line) == {"ts": 1.0, "type": "judge_start", "total": 3}


def test_parse_line_tolerates_garbage():
    assert je.parse_line("") is None
    assert je.parse_line("   ") is None
    assert je.parse_line('{"truncated": ') is None  # half-written line
    assert je.parse_line("[1,2,3]") is None  # not a dict
    assert je.parse_line('{"no":"type"}') is None  # missing type tag


def test_build_view_empty():
    v = je.build_view([])
    assert v.total == 0 and v.done == 0 and v.eta_s is None and v.finished is False
    assert v.mean_score is None and v.red_flags == 0
    assert v.histogram == {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}


def test_build_view_histogram_mean_red_and_unscored():
    events = [
        je.judge_start_event(0.0, 4),
        je.verdict_event(0.1, 0, "m", "v", "p1", 0, "A", 4, False, False, "ok"),
        je.verdict_event(0.2, 1, "m", "v", "p2", 0, "A", 2, True, False, "unsicher"),
        je.verdict_event(0.3, 2, "m", "v", "p3", 0, "B", 0, False, True, "judge weg"),
    ]
    v = je.build_view(events)
    assert v.total == 4 and v.done == 3
    assert v.histogram == {1: 0, 2: 1, 3: 0, 4: 1, 5: 0}  # unscored excluded
    assert v.red_flags == 1
    assert v.mean_score == 3.0  # mean of [4, 2], unscored excluded
    assert v.finished is False


def test_build_view_dedups_on_resume_replay():
    # same cell judged twice (crash+resume) -> counts once, last wins
    events = [
        je.judge_start_event(0.0, 2),
        je.verdict_event(0.1, 0, "m", "v", "p1", 0, "A", 2, False, False, "first"),
        je.verdict_event(0.2, 0, "m", "v", "p1", 0, "A", 5, False, False, "second"),
    ]
    v = je.build_view(events)
    assert v.done == 1 and v.histogram[5] == 1 and v.histogram[2] == 0
    assert v.verdicts[0].rationale == "second"


def test_build_view_eta_ignores_replay_burst():
    # prior burst written ~instantly (tiny gaps) must not crush the ETA
    events = [
        je.judge_start_event(0.0, 5),
        je.verdict_event(100.00, 0, "m", "v", "p1", 0, "A", 3, False, False, "a"),
        je.verdict_event(100.00, 1, "m", "v", "p2", 0, "A", 3, False, False, "b"),  # burst
        je.verdict_event(110.0, 2, "m", "v", "p3", 0, "A", 3, False, False, "c"),  # 10s real gap
    ]
    v = je.build_view(events)
    assert v.done == 3
    assert v.eta_s is not None and abs(v.eta_s - 20.0) < 0.01  # 10s/gap x 2 remaining


def test_build_view_masters_and_finished():
    events = [
        je.judge_start_event(0.0, 1),
        je.verdict_event(0.1, 0, "m", "v", "p1", 0, "A", 3, False, False, "x"),
        je.master_event(0.2, "m", "v", 40.0, False, "Q6 <= 2", "Nein"),
        je.judge_done_event(0.3, 1, 1),
    ]
    v = je.build_view(events)
    assert v.finished is True and len(v.masters) == 1
    assert v.masters[0].pct == 40.0 and v.masters[0].safety_passed is False
    assert v.masters[0].recommendation == "Nein"
    d = v.as_dict()
    assert d["histogram"] == {"1": 0, "2": 0, "3": 1, "4": 0, "5": 0}  # str keys for JSON
    assert d["masters"][0]["safety_reason"] == "Q6 <= 2"
