from ramcheck.config import Config
from ramcheck.pack import Pack
from ramcheck.qualrun import iter_eval_cells, run_eval
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
