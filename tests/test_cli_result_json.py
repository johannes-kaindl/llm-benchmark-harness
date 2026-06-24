"""Integration test: cli writes result.json after eval and rewrites it after judge.

Uses the same fake-bundle pattern as test_cli_judge_web.py — no real endpoint needed.
"""

from __future__ import annotations

import json

import typer.testing

from touchstone import cli
from touchstone.results import EvalResponse

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_MIN_PACK_YAML = """\
id: demo
title: Demo
scale: {1: a, 2: b, 3: c, 4: d, 5: e}
dimensions:
  - {id: Q1, name: Korrektheit, weight: 3}
  - {id: Q6, name: Sicherheit, weight: 3}
ko_rule: {dimension: Q6, threshold: 2, red_flag_prompts: [A1]}
prompt_variants:
  - {id: none, system_prompt: null}
categories:
  - id: A
    name: ADHS
    prompts:
      - {id: A1, title: t, prompt: p, green_flags: [g], red_flags: [r]}
"""

_MIN_CONFIG_YAML = """\
endpoint:
  base_url: http://localhost:9/v1
models:
  - id: fake-model
    quant: Q4_K_M
output_dir: {out}
"""


def _min_response(pack_path: str) -> EvalResponse:
    return EvalResponse(
        pack_id="demo",
        pack_version=1,
        machine="M",
        model="fake-model",
        quant="Q4_K_M",
        engine="lm-studio",
        engine_version="0.3",
        variant="none",
        category="A",
        prompt_id="A1",
        repeat=0,
        response_text="some answer",
        content_empty=False,
        ttft_s=0.1,
        decode_tps=10.0,
        prefill_tps=100.0,
        e2e_s=1.0,
        prompt_tokens=10,
        completion_tokens=20,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=None,
        sys_used_mb=None,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.0,
    )


def _write_eval_bundle(tmp_path):
    """Create a minimal, already-finished eval bundle on disk."""
    pack_yaml = tmp_path / "pack.yaml"
    pack_yaml.write_text(_MIN_PACK_YAML, encoding="utf-8")
    resp = _min_response(str(pack_yaml))
    (tmp_path / "responses.jsonl").write_text(
        json.dumps(resp.as_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (tmp_path / "bundle.json").write_text(
        json.dumps(
            {
                "pack_path": str(pack_yaml),
                "pack_id": "demo",
                "pack_version": 1,
                "models": [{"id": "fake-model", "quant": "Q4_K_M"}],
                "variants": ["none"],
                "host": {"chip": "M5", "ram_gb": "64 GB", "macos": "15.0"},
                "sampling": {"temperature": 0.0, "seed": 42},
                "date": "2026-06-24",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (tmp_path / "judge.yaml").write_text(
        "endpoint:\n  base_url: http://localhost:9/v1\nmodel: fake-judge\ntemperature: 0.0\n",
        encoding="utf-8",
    )
    return tmp_path


class _FakeJudgeBackend:
    def __init__(self, *args, **kwargs):
        pass

    def judge(self, *, system: str, user: str) -> str:
        return '{"score": 4, "red_flag": false, "rationale": "ok", "Q1": 4, "Q6": 4}'


class _FakeEvalClient:
    """Minimal fake client for the eval command."""

    engine = "fake"
    engine_version = "1.0"

    def stream(self, *, messages, model, max_tokens, temperature, seed):
        from touchstone.runner import StreamEvent

        yield StreamEvent(delta_text="answer")
        yield StreamEvent(prompt_tokens=5, completion_tokens=10)

    def probe_build_metadata(self):
        from touchstone.client import BuildMetadata

        return BuildMetadata(engine_version="1.0", runtime="llama", quant_by_model={})

    def list_models(self) -> list[str]:
        return ["fake-model"]


def _run_eval_cmd(tmp_path):
    """Invoke `touchstone eval` and return the run_dir."""
    pack = tmp_path / "pack.yaml"
    pack.write_text(_MIN_PACK_YAML, encoding="utf-8")
    cfg_yaml = tmp_path / "config.yaml"
    cfg_yaml.write_text(
        _MIN_CONFIG_YAML.replace("{out}", str(tmp_path / "runs")), encoding="utf-8"
    )
    runner = typer.testing.CliRunner()
    result = runner.invoke(
        cli.app,
        ["eval", "--pack", str(pack), "--config", str(cfg_yaml)],
    )
    assert result.exit_code == 0, f"eval failed:\n{result.output}"
    # The run dir is named <timestamp>_eval_demo inside runs/
    runs = list((tmp_path / "runs").iterdir())
    assert runs, "no run dir created"
    return runs[0]


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_eval_writes_result_json_perf_only(tmp_path, monkeypatch):
    """After eval, result.json must exist with schema_version=1 and quality=null in cells."""
    monkeypatch.setattr("touchstone.cli._make_client", lambda cfg: _FakeEvalClient())
    run_dir = _run_eval_cmd(tmp_path)
    rj = run_dir / "result.json"
    assert rj.exists(), "result.json not written by eval"
    doc = json.loads(rj.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert "provenance" in doc
    assert "cells" in doc
    assert len(doc["cells"]) >= 1
    assert doc["cells"][0]["quality"] is None, "quality should be null after perf-only eval"


def test_eval_then_judge_writes_result_json(tmp_path, monkeypatch):
    """Full pipeline: eval writes perf-only result.json; judge rewrites it with quality + judge."""
    monkeypatch.setattr("touchstone.cli._make_client", lambda cfg: _FakeEvalClient())
    run_dir = _run_eval_cmd(tmp_path)

    # Verify eval wrote perf-only result.json
    doc = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert doc["cells"][0]["quality"] is None  # perf-only after eval

    # Now run judge on the bundle
    monkeypatch.setattr("touchstone.cli.OpenAIJudgeBackend", _FakeJudgeBackend)
    judge_yaml = tmp_path / "judge.yaml"
    judge_yaml.write_text(
        "endpoint:\n  base_url: http://localhost:9/v1\nmodel: fake-judge\ntemperature: 0.0\n",
        encoding="utf-8",
    )
    runner = typer.testing.CliRunner()
    result = runner.invoke(
        cli.app,
        ["judge", "--bundle", str(run_dir), "--judge-config", str(judge_yaml)],
    )
    assert result.exit_code == 0, f"judge failed:\n{result.output}"

    doc = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert "provenance" in doc
    assert doc["cells"][0]["quality"] is not None, "quality should be filled after judging"
    assert doc["cells"][0]["quality"]["rubric_level"], "rubric_level must be non-empty"
    assert doc["judge"] is not None
    assert doc["judge"]["model"] == "fake-judge"


def test_judge_standalone_writes_result_json(tmp_path, monkeypatch):
    """Judge on a pre-built bundle (no eval-written result.json) still works."""
    bundle = _write_eval_bundle(tmp_path)
    monkeypatch.setattr("touchstone.cli.OpenAIJudgeBackend", _FakeJudgeBackend)
    result = typer.testing.CliRunner().invoke(
        cli.app,
        ["judge", "--bundle", str(bundle), "--judge-config", str(bundle / "judge.yaml")],
    )
    assert result.exit_code == 0, f"judge failed:\n{result.output}"
    rj = bundle / "result.json"
    assert rj.exists(), "result.json not written by judge"
    doc = json.loads(rj.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1
    assert doc["judge"]["model"] == "fake-judge"
    assert doc["cells"][0]["quality"]["rubric_level"]
