import json

from ramcheck.loadview import latest_load


def _write(p, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_missing_or_empty_returns_none(tmp_path):
    assert latest_load(tmp_path / "nope.jsonl") is None
    p = tmp_path / "r.jsonl"
    p.write_text("", encoding="utf-8")
    assert latest_load(p) is None


def test_returns_latest_sample_and_throttle_history(tmp_path):
    p = tmp_path / "r.jsonl"
    _write(
        p,
        [
            {"ts": 1.0, "sys_used_mb": 1000.0, "mem_pressure_level": "normal", "throttled": False},
            {"ts": 2.0, "sys_used_mb": 2000.0, "mem_pressure_level": "warn", "throttled": True},
            {"ts": 3.0, "sys_used_mb": 1500.0, "mem_pressure_level": "normal", "throttled": False},
        ],
    )
    lv = latest_load(p)
    assert lv is not None
    assert lv.sys_used_mb == 1500.0 and lv.mem_pressure == "normal"
    assert lv.throttled is False and lv.any_throttle_seen is True  # throttled earlier


def test_tolerates_partial_last_line(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(
        json.dumps({"ts": 1.0, "sys_used_mb": 9.0, "mem_pressure_level": "x", "throttled": False})
        + "\n"
        + '{"ts": 2.0, "sys',  # half-written
        encoding="utf-8",
    )
    lv = latest_load(p)
    assert lv is not None and lv.sys_used_mb == 9.0
