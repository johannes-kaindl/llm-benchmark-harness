# tests/test_gui_app_control.py
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunHandle, RunInProgress, RunRegistry


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


# ---------------------------------------------------------------------------
# /runs/eval
# ---------------------------------------------------------------------------


def test_start_eval_calls_registry(tmp_path):
    """Happy path: eval start returns 200 with run_dir and kind."""

    class _Reg(RunRegistry):
        def start_eval(self, *, pack_path, config_path, resume_dir=None):
            return RunHandle("eval", tmp_path / "x", 1)

    reg = _Reg(runs_dir=tmp_path, launcher=_FakeLauncher())
    r = TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg)).post(
        "/runs/eval", data={"pack_path": "packs/ndassist.yaml", "config_path": "config.m5.yaml"}
    )
    assert r.status_code in (200, 303)
    assert r.json()["kind"] == "eval"


def test_start_eval_passes_resume_dir(tmp_path):
    """A posted resume_dir must reach start_eval as resume_dir=Path(...) (FUNCTIONAL)."""
    resume = tmp_path / "2026-06-20_eval_ndassist"
    resume.mkdir()
    seen = {}

    class _Reg(RunRegistry):
        def start_eval(self, *, pack_path, config_path, resume_dir=None):
            seen["resume_dir"] = resume_dir
            return RunHandle("eval", resume_dir or (tmp_path / "x"), 1)

    reg = _Reg(runs_dir=tmp_path, launcher=_FakeLauncher())
    r = TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg)).post(
        "/runs/eval",
        data={
            "pack_path": "packs/ndassist.yaml",
            "config_path": "config.m5.yaml",
            "resume_dir": resume.name,
        },
    )
    assert r.status_code == 200
    assert seen["resume_dir"] == resume.resolve()


def test_start_eval_rejects_traversal_pack_path(tmp_path):
    """An absolute or traversing pack_path must be rejected (SECURITY a)."""
    client = _client(tmp_path)
    r1 = client.post(
        "/runs/eval",
        data={"pack_path": "/etc/passwd", "config_path": "config.m5.yaml"},
    )
    assert r1.status_code in (404, 422)
    r2 = client.post(
        "/runs/eval",
        data={"pack_path": "../../etc/passwd", "config_path": "config.m5.yaml"},
    )
    assert r2.status_code in (404, 422)


def test_start_eval_rejects_resume_dir_traversal(tmp_path):
    """A resume_dir escaping runs_dir must be rejected (SECURITY a / FUNCTIONAL)."""
    r = _client(tmp_path).post(
        "/runs/eval",
        data={
            "pack_path": "packs/ndassist.yaml",
            "config_path": "config.m5.yaml",
            "resume_dir": "../../etc",
        },
    )
    assert r.status_code in (404, 422)


def test_start_blocked_returns_conflict(tmp_path):
    """409 is returned when a run is already in progress."""

    class _Busy(RunRegistry):
        def start_eval(self, **k):
            raise RunInProgress("busy")

    reg = _Busy(runs_dir=tmp_path, launcher=_FakeLauncher())
    r = TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg)).post(
        "/runs/eval", data={"pack_path": "p", "config_path": "c"}
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# /runs/judge
# ---------------------------------------------------------------------------


def test_start_judge_calls_registry(tmp_path):
    """Happy path: judge start returns 200 with run_dir and kind='judge'."""
    bundle_dir = tmp_path / "2026-06-20_eval_ndassist"
    bundle_dir.mkdir()

    class _Reg(RunRegistry):
        def start_judge(self, *, bundle, judge_config_path):
            return RunHandle("judge", bundle, 2)

    reg = _Reg(runs_dir=tmp_path, launcher=_FakeLauncher())
    r = TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg)).post(
        "/runs/judge",
        data={"bundle": bundle_dir.name, "judge_config_path": "judge.yaml"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "judge"
    assert data["run_dir"] == bundle_dir.name


def test_start_judge_rejects_traversal(tmp_path):
    """Path traversal in bundle name must be rejected with 404."""
    r = _client(tmp_path).post(
        "/runs/judge",
        data={"bundle": "../../etc", "judge_config_path": "j.yaml"},
    )
    assert r.status_code == 404


def test_start_judge_conflict(tmp_path):
    """409 when a run is active."""
    bundle_dir = tmp_path / "run1"
    bundle_dir.mkdir()

    class _Busy(RunRegistry):
        def start_judge(self, **k):
            raise RunInProgress("busy")

    reg = _Busy(runs_dir=tmp_path, launcher=_FakeLauncher())
    r = TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg)).post(
        "/runs/judge",
        data={"bundle": bundle_dir.name, "judge_config_path": "j.yaml"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# /runs/stop
# ---------------------------------------------------------------------------


def _mk_sentinel(run_dir, pid=42):
    """Write a minimal run.json sentinel so stop_run can find a PID."""
    from ramcheck.gui.control import write_sentinel

    write_sentinel(run_dir, kind="eval", pid=pid, pack_path="p", config_path="c")


def test_stop_run_terminates(tmp_path):
    """Happy path: stop marks the run stopped and returns {stopped: <name>}."""
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    _mk_sentinel(run_dir, pid=9999)

    stopped = []

    class _Reg(RunRegistry):
        def stop(self, handle):
            stopped.append(handle)

    reg = _Reg(runs_dir=tmp_path, launcher=_FakeLauncher())
    r = TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg)).post(
        "/runs/stop", data={"name": run_dir.name}
    )
    assert r.status_code == 200
    assert r.json() == {"stopped": run_dir.name}
    assert stopped and stopped[0].pid == 9999


def test_stop_run_missing_sentinel(tmp_path):
    """404 when the named run dir has no sentinel."""
    run_dir = tmp_path / "ghost"
    run_dir.mkdir()
    r = _client(tmp_path).post("/runs/stop", data={"name": run_dir.name})
    assert r.status_code == 404


def test_stop_run_rejects_traversal(tmp_path):
    """Path traversal in name must be rejected with 404."""
    r = _client(tmp_path).post("/runs/stop", data={"name": "../../etc"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /live/{name} — SSE stream
# ---------------------------------------------------------------------------


def test_live_stream_eval_sse(tmp_path):
    """SSE endpoint yields at least one event: view frame, then stops on finished."""
    from ramcheck import events as ev

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    events_file = run_dir / "events.jsonl"
    # Write a minimal finished run so the generator exits after one frame.
    lines = [
        ev.dumps(ev.run_start_event(1.0, 1)),
        ev.dumps(ev.cell_start_event(1.1, 0, "m", "v", "A", "A1", 0)),
        ev.dumps(ev.cell_done_event(1.2, 0, "m", "v", "A1", 0, True, 0.3, 1.0, 5.0, 7, False, "")),
        ev.dumps(ev.run_done_event(2.0, 1, 1)),
    ]
    events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = _client(tmp_path).get(f"/live/{run_dir.name}?kind=eval")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "no-cache" in r.headers.get("cache-control", "")
    body = r.text
    # Each frame must be "event: view\ndata: {...}\n\n"
    assert "event: view" in body
    assert "data: " in body
    # The data must be valid JSON containing expected keys.
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
            assert "total" in payload
            break


def test_live_stream_judge_sse(tmp_path):
    """SSE endpoint with kind=judge uses judge_events.jsonl and reaches finished."""
    from ramcheck import judge_events as je

    run_dir = tmp_path / "run_j"
    run_dir.mkdir()
    judge_file = run_dir / "judge_events.jsonl"
    # Include judge_done_event so finished=True and the generator exits cleanly.
    lines = [
        je.dumps(je.judge_start_event(1.0, 2)),
        je.dumps(je.judge_done_event(2.0, 2, 2)),
    ]
    judge_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = _client(tmp_path).get(f"/live/{run_dir.name}?kind=judge")
    assert r.status_code == 200
    body = r.text
    assert "event: view" in body
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
            assert payload["total"] == 2
            break


def test_live_stream_rejects_traversal(tmp_path):
    """Path traversal in live/{name} must be rejected with 404."""
    r = _client(tmp_path).get("/live/../../etc")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# CSRF / DNS-rebinding defense (SECURITY b)
# ---------------------------------------------------------------------------


def test_foreign_host_rejected(tmp_path):
    """TrustedHostMiddleware rejects a foreign Host header (DNS-rebinding)."""
    r = _client(tmp_path).get("/", headers={"host": "evil.example.com"})
    assert r.status_code == 400


def test_localhost_host_allowed(tmp_path):
    """localhost / 127.0.0.1 Host headers are allowed for the single-user UX."""
    c = _client(tmp_path)
    assert c.get("/", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert c.get("/", headers={"host": "localhost:8000"}).status_code == 200


def test_post_foreign_origin_rejected(tmp_path):
    """A state-changing POST with a cross-site Origin is rejected (CSRF)."""
    r = _client(tmp_path).post(
        "/runs/eval",
        data={"pack_path": "packs/ndassist.yaml", "config_path": "config.m5.yaml"},
        headers={"origin": "http://evil.example.com"},
    )
    assert r.status_code == 403


def test_post_local_origin_allowed(tmp_path):
    """A POST whose Origin matches the local server is allowed."""

    class _Reg(RunRegistry):
        def start_eval(self, *, pack_path, config_path, resume_dir=None):
            return RunHandle("eval", tmp_path / "x", 1)

    reg = _Reg(runs_dir=tmp_path, launcher=_FakeLauncher())
    r = TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg)).post(
        "/runs/eval",
        data={"pack_path": "packs/ndassist.yaml", "config_path": "config.m5.yaml"},
        headers={"origin": "http://localhost:8000"},
    )
    assert r.status_code == 200


def _dead_pid():
    """A pid that is (almost certainly) not alive."""
    import os

    pid = 2_000_000_000
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            pass
        pid -= 1


def test_live_stream_crashed_subprocess_terminates(tmp_path):
    """A crashed run (sentinel pid dead, no run_done) must terminate the SSE stream
    with a terminal 'crashed' frame instead of busy-looping forever (MAJOR 2)."""
    from ramcheck import events as ev
    from ramcheck.gui.control import write_sentinel

    run_dir = tmp_path / "run_crash"
    run_dir.mkdir()
    # A non-terminal event stream: started, one cell done, but NO run_done.
    lines = [
        ev.dumps(ev.run_start_event(1.0, 2)),
        ev.dumps(ev.cell_done_event(1.2, 0, "m", "v", "A1", 0, True, 0.3, 1.0, 5.0, 7, False, "")),
    ]
    (run_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Sentinel says running but its pid is dead → is_active() is False.
    write_sentinel(run_dir, kind="eval", pid=_dead_pid(), pack_path="p", config_path="c")

    r = _client(tmp_path).get(f"/live/{run_dir.name}?kind=eval")
    assert r.status_code == 200
    body = r.text
    # The generator must have terminated (TestClient drained the whole body) and
    # emitted a terminal frame flagged as crashed.
    crashed_seen = False
    finished_seen = False
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: ") :])
            if payload.get("crashed"):
                crashed_seen = True
            if payload.get("finished"):
                finished_seen = True
    assert crashed_seen, body
    # finished must be truthy on the terminal frame so the client stops polling.
    assert finished_seen, body
