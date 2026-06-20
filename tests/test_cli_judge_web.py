import json

from ramcheck import cli
from ramcheck.results import Verdict


def _v(prompt_id, score, red=False, unscored=False, model="m", variant="v"):
    return Verdict(
        model=model,
        variant=variant,
        prompt_id=prompt_id,
        repeat=0,
        category="A",
        score=score,
        red_flag=red,
        rationale="r-" + prompt_id,
        unscored=unscored,
    )


def _read(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_judge_event_writers_start_replays_prior(tmp_path):
    p = tmp_path / "judge_events.jsonl"
    on_start, _on_verdict, _write_masters, _done = cli._judge_event_writers(p)
    on_start(3, [_v("p1", 4), _v("p2", 2, red=True)])
    rows = _read(p)
    assert rows[0]["type"] == "judge_start" and rows[0]["total"] == 3
    assert [r["type"] for r in rows[1:]] == ["verdict", "verdict"]
    assert rows[1]["i"] == 0 and rows[2]["i"] == 1 and rows[2]["red_flag"] is True


def test_judge_event_writers_fresh_verdict_continues_counter(tmp_path):
    p = tmp_path / "judge_events.jsonl"
    on_start, on_verdict, _write_masters, _done = cli._judge_event_writers(p)
    on_start(2, [_v("p1", 4)])
    on_verdict(_v("p2", 5))
    rows = _read(p)
    verdicts = [r for r in rows if r["type"] == "verdict"]
    assert verdicts[1]["i"] == 1 and verdicts[1]["prompt_id"] == "p2"


def test_judge_event_writers_masters_and_done(tmp_path):
    p = tmp_path / "judge_events.jsonl"
    on_start, _on_verdict, write_masters, done = cli._judge_event_writers(p)
    on_start(1, [])
    write_masters(
        [
            {
                "model": "m",
                "variant": "v",
                "pct": 40.0,
                "safety_passed": False,
                "safety_reason": "Q6",
                "recommendation": "Nein",
            }
        ]
    )
    done(1, 1)
    rows = _read(p)
    assert rows[-2]["type"] == "master" and rows[-2]["pct"] == 40.0
    assert rows[-1]["type"] == "judge_done" and rows[-1]["scored"] == 1
