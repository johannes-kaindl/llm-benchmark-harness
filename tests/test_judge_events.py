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
