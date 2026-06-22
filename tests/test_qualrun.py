import pytest

from ramcheck.config import Config
from ramcheck.pack import Pack
from ramcheck.qualrun import iter_eval_cells, load_responses_jsonl, run_eval
from ramcheck.runner import StreamEvent


def _config():
    return Config.model_validate(
        {
            "endpoint": {"base_url": "http://localhost:11434/v1"},
            "machine": "M-test",
            "models": [{"id": "m1", "quant": "n/a"}],
        }
    )


def _pack():
    return Pack.model_validate(
        {
            "id": "demo",
            "title": "Demo",
            "scale": {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"},
            "dimensions": [{"id": "Q1", "name": "K", "weight": 3}],
            "ko_rule": {"dimension": "Q1", "threshold": 2},
            "prompt_variants": [
                {"id": "baseline", "system_prompt": "be kind"},
                {"id": "none", "system_prompt": None},
            ],
            "categories": [
                {"id": "A", "name": "ADHS", "prompts": [{"id": "A1", "title": "t", "prompt": "p"}]},
                {
                    "id": "E",
                    "name": "Safety",
                    "prompts": [{"id": "E1", "title": "t", "prompt": "p", "repeats": 2}],
                },
            ],
        }
    )


class FakeClient:
    engine = "fake"
    engine_version = "0"

    def __init__(self, text="answer", emit_text=True):
        self.text = text
        self.emit_text = emit_text

    def stream(self, *, messages, model, max_tokens, temperature, seed):
        if self.emit_text:
            yield StreamEvent(delta_text=self.text)
        yield StreamEvent(prompt_tokens=10, completion_tokens=5)


class NoopSampler:
    def start(self):
        pass

    def stop(self):
        pass


def test_iter_eval_cells_counts_matrix_with_repeats():
    cells = iter_eval_cells(_config(), _pack())
    # 1 model × 2 variants × (A1 ×1 + E1 ×2) = 2 × 3 = 6
    assert len(cells) == 6
    e1_repeats = sorted(c.repeat for c in cells if c.prompt.id == "E1" and c.variant.id == "none")
    assert e1_repeats == [0, 1]


def test_run_eval_produces_one_response_per_cell(tmp_path):
    responses = run_eval(
        _config(), _pack(), FakeClient(text="hello"), run_dir=tmp_path, sampler=NoopSampler()
    )
    assert len(responses) == 6
    assert all(r.response_text == "hello" for r in responses)
    assert all(r.content_empty is False for r in responses)
    assert {r.prompt_id for r in responses} == {"A1", "E1"}
    assert {r.variant for r in responses} == {"baseline", "none"}
    assert sum(1 for r in responses if r.is_cold_start) == 1  # exactly one cold-start


def test_run_eval_flags_empty_content(tmp_path):
    responses = run_eval(
        _config(),
        _pack(),
        FakeClient(emit_text=False),  # only usage, no text → reasoning-only / empty
        run_dir=tmp_path,
        sampler=NoopSampler(),
    )
    assert all(r.content_empty is True for r in responses)


def test_run_eval_writes_bundle_files(tmp_path):
    run_eval(_config(), _pack(), FakeClient(), run_dir=tmp_path, sampler=NoopSampler())
    assert (tmp_path / "responses.jsonl").exists()
    assert (tmp_path / "perf.csv").exists()
    lines = (tmp_path / "responses.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 6


class CountingClient:
    engine = "fake"
    engine_version = "0"

    def __init__(self):
        self.calls = 0

    def stream(self, *, messages, model, max_tokens, temperature, seed):
        self.calls += 1
        yield StreamEvent(delta_text="x")
        yield StreamEvent(prompt_tokens=10, completion_tokens=5)


class _Crash(BaseException):
    """Not an Exception → not swallowed by stream_once; simulates a hard interrupt."""


class CrashAfterClient:
    engine = "fake"
    engine_version = "0"

    def __init__(self, crash_on):
        self.crash_on = crash_on
        self.calls = 0

    def stream(self, *, messages, model, max_tokens, temperature, seed):
        self.calls += 1
        if self.calls > self.crash_on:
            raise _Crash("boom")
        yield StreamEvent(delta_text="x")
        yield StreamEvent(prompt_tokens=10, completion_tokens=5)


def test_run_eval_resume_skips_done_cells(tmp_path):
    cfg, pack = _config(), _pack()
    run_eval(cfg, pack, FakeClient(), run_dir=tmp_path, sampler=NoopSampler())  # full: 6 cells
    client = CountingClient()
    responses = run_eval(cfg, pack, client, run_dir=tmp_path, sampler=NoopSampler(), resume=True)
    assert client.calls == 0  # everything already done → no generation
    assert len(responses) == 6  # all loaded from disk


def test_run_eval_appends_incrementally_so_a_crash_keeps_done_work(tmp_path):
    cfg, pack = _config(), _pack()
    # crash_on=3: call 1 = preflight smoke (not matrix), calls 2+3 = first two matrix cells
    # (written to disk), call 4 crashes before the 3rd matrix cell is written.
    client = CrashAfterClient(crash_on=3)
    with pytest.raises(_Crash):
        run_eval(cfg, pack, client, run_dir=tmp_path, sampler=NoopSampler())
    lines = (tmp_path / "responses.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # the two finished cells were persisted before the crash


def test_run_eval_resume_after_crash_completes_the_rest(tmp_path):
    cfg, pack = _config(), _pack()
    # crash_on=3: 2 matrix cells done before crash (call 1 = preflight, calls 2+3 = matrix)
    with pytest.raises(_Crash):
        run_eval(cfg, pack, CrashAfterClient(crash_on=3), run_dir=tmp_path, sampler=NoopSampler())
    # resume with a healthy client → only the remaining 4 cells run (resume skips preflight)
    client = CountingClient()
    responses = run_eval(cfg, pack, client, run_dir=tmp_path, sampler=NoopSampler(), resume=True)
    assert client.calls == 4
    assert len(responses) == 6


def test_run_eval_records_reasoning_chars(tmp_path):
    class ReasoningClient:
        engine = "fake"
        engine_version = "0"

        def stream(self, *, messages, model, max_tokens, temperature, seed):
            yield StreamEvent(reasoning_text="abcde")  # 5 chars of "thinking"
            yield StreamEvent(delta_text="hi")
            yield StreamEvent(prompt_tokens=1, completion_tokens=1)

    responses = run_eval(
        _config(), _pack(), ReasoningClient(), run_dir=tmp_path, sampler=NoopSampler()
    )
    assert all(r.reasoning_chars == 5 for r in responses)
    assert all(r.content_empty is False for r in responses)  # content present alongside reasoning


def test_run_eval_fires_progress_callbacks(tmp_path):
    starts, cells_started, cells_done = [], [], []
    run_eval(
        _config(),
        _pack(),
        FakeClient(text="hi"),
        run_dir=tmp_path,
        sampler=NoopSampler(),
        on_run_start=lambda total: starts.append(total),
        on_cell_start=lambda i, cell: cells_started.append((i, cell.prompt.id)),
        on_cell_done=lambda i, resp: cells_done.append((i, resp.prompt_id, resp.ok)),
    )
    assert starts == [6]  # total reported once, before the loop
    assert len(cells_started) == 6
    assert len(cells_done) == 6
    assert all(ok for _, _, ok in cells_done)
    assert [i for i, _ in cells_started] == [0, 1, 2, 3, 4, 5]  # enumerate index


def test_run_eval_callbacks_skip_resumed_cells(tmp_path):
    cfg, pack = _config(), _pack()
    run_eval(cfg, pack, FakeClient(), run_dir=tmp_path, sampler=NoopSampler())  # all 6 done
    started = []
    run_eval(
        cfg,
        pack,
        FakeClient(),
        run_dir=tmp_path,
        sampler=NoopSampler(),
        resume=True,
        on_cell_start=lambda i, cell: started.append(i),
    )
    assert started == []  # every cell already done → no cell_start fired


def test_load_responses_jsonl_tolerates_bad_last_line(tmp_path):
    run_eval(_config(), _pack(), FakeClient(), run_dir=tmp_path, sampler=NoopSampler())
    p = tmp_path / "responses.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"truncated": ')  # a half-written final line (crash mid-write)
    out = load_responses_jsonl(p)
    assert len(out) == 6  # 6 good lines load; the partial line is skipped


class CapturingClient:
    engine = "fake"
    engine_version = "0"

    def __init__(self):
        self.seen = []

    def stream(self, *, messages, model, max_tokens, temperature, seed, extra_body=None):
        self.seen.append({"max_tokens": max_tokens, "extra_body": extra_body})
        # reasoning-only: no delta_text, but reasoning present
        from ramcheck.runner import StreamEvent

        yield StreamEvent(reasoning_text="denke nach…")
        yield StreamEvent(prompt_tokens=10, completion_tokens=5)


def _config_with(model_dict):
    return Config.model_validate(
        {
            "endpoint": {"base_url": "http://localhost:11434/v1"},
            "machine": "M-test",
            "models": [model_dict],
        }
    )


def test_run_eval_adds_reasoning_headroom_to_budget(tmp_path):
    client = CapturingClient()
    cfg = _config_with(
        {"id": "m1", "reasoning_headroom_tokens": 1000, "extra_body": {"enable_thinking": False}}
    )
    run_eval(cfg, _pack(), client, run_dir=tmp_path, sampler=NoopSampler())
    # pack prompts default max_tokens=400; effective = 400 + 1000
    assert all(s["max_tokens"] == 1400 for s in client.seen)
    assert all(s["extra_body"] == {"enable_thinking": False} for s in client.seen)


def test_run_eval_persists_reasoning_text_only_when_empty(tmp_path):
    client = CapturingClient()  # reasoning-only → content_empty
    responses = run_eval(
        _config_with({"id": "m1"}), _pack(), client, run_dir=tmp_path, sampler=NoopSampler()
    )
    assert all(r.content_empty for r in responses)
    assert all(r.reasoning_text == "denke nach…" for r in responses)
    # and a content answer must NOT carry reasoning_text
    responses2 = run_eval(
        _config_with({"id": "m2"}),
        _pack(),
        FakeClient(text="hi"),
        run_dir=tmp_path / "b",
        sampler=NoopSampler(),
    )
    assert all(r.reasoning_text == "" for r in responses2)


def test_run_eval_runs_preflight_and_calls_callback(tmp_path):
    seen = {}

    def on_pf(results):
        seen["results"] = results

    run_eval(
        _config_with({"id": "m1"}),
        _pack(),
        FakeClient(text="hi"),
        run_dir=tmp_path,
        sampler=NoopSampler(),
        on_preflight=on_pf,
    )
    assert "results" in seen
    assert seen["results"][0].status == "ok"


def test_run_eval_strict_preflight_aborts_on_reasoning_only(tmp_path):
    client = CapturingClient()  # reasoning-only
    with pytest.raises(RuntimeError, match=r"Pre-Flight.*m1.*reasoning_only"):
        run_eval(
            _config_with({"id": "m1"}),
            _pack(),
            client,
            run_dir=tmp_path,
            sampler=NoopSampler(),
            strict_preflight=True,
        )


def test_run_eval_resume_skips_preflight(tmp_path):
    # seed a done bundle first
    run_eval(
        _config_with({"id": "m1"}),
        _pack(),
        FakeClient(text="hi"),
        run_dir=tmp_path,
        sampler=NoopSampler(),
    )
    called = {"n": 0}
    run_eval(
        _config_with({"id": "m1"}),
        _pack(),
        FakeClient(text="hi"),
        run_dir=tmp_path,
        sampler=NoopSampler(),
        resume=True,
        on_preflight=lambda r: called.__setitem__("n", called["n"] + 1),
    )
    assert called["n"] == 0
