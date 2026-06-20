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
    client = CrashAfterClient(crash_on=2)
    with pytest.raises(_Crash):
        run_eval(cfg, pack, client, run_dir=tmp_path, sampler=NoopSampler())
    lines = (tmp_path / "responses.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # the two finished cells were persisted before the crash


def test_run_eval_resume_after_crash_completes_the_rest(tmp_path):
    cfg, pack = _config(), _pack()
    with pytest.raises(_Crash):
        run_eval(cfg, pack, CrashAfterClient(crash_on=2), run_dir=tmp_path, sampler=NoopSampler())
    # resume with a healthy client → only the remaining 4 cells run
    client = CountingClient()
    responses = run_eval(cfg, pack, client, run_dir=tmp_path, sampler=NoopSampler(), resume=True)
    assert client.calls == 4
    assert len(responses) == 6


def test_run_eval_fires_progress_callbacks(tmp_path):
    starts, cells_started, cells_done = [], [], []
    run_eval(
        _config(), _pack(), FakeClient(text="hi"), run_dir=tmp_path, sampler=NoopSampler(),
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
        cfg, pack, FakeClient(), run_dir=tmp_path, sampler=NoopSampler(), resume=True,
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
