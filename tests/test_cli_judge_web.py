import contextlib
import json

import typer.testing

from ramcheck import cli
from ramcheck.results import EvalResponse, Verdict


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


def test_judge_done_closes_file_and_flushes(tmp_path):
    p = tmp_path / "judge_events.jsonl"
    on_start, on_verdict, _write_masters, done = cli._judge_event_writers(p)
    on_start(1, [])
    on_verdict(_v("p1", 3))
    done(1, 1)
    rows = _read(p)  # readable in full only if flushed + closed
    assert [r["type"] for r in rows] == ["judge_start", "verdict", "judge_done"]


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


def _min_response():
    return EvalResponse(
        pack_id="demo",
        pack_version=1,
        machine="M",
        model="m",
        quant="n/a",
        engine="ollama",
        engine_version="0",
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


def _write_min_bundle(tmp_path):
    pack = tmp_path / "pack.yaml"
    pack.write_text(_MIN_PACK_YAML, encoding="utf-8")
    (tmp_path / "responses.jsonl").write_text(
        json.dumps(_min_response().as_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (tmp_path / "bundle.json").write_text(
        json.dumps({"pack_path": str(pack), "host": {"chip": "x", "ram_gb": "8", "macos": "14"}}),
        encoding="utf-8",
    )
    (tmp_path / "judge.yaml").write_text(
        "endpoint:\n  base_url: http://localhost:9/v1\nmodel: fake\n", encoding="utf-8"
    )
    return tmp_path


class _FakeJudgeBackend:
    def __init__(self, *args, **kwargs):
        pass

    def judge(self, *, system: str, user: str) -> str:
        # covers both per-prompt scoring (score/red_flag/rationale) and
        # dimension scoring (Q1/Q6 are the dimension IDs in _MIN_PACK_YAML)
        return '{"score": 4, "red_flag": false, "rationale": "ok", "Q1": 4, "Q6": 4}'


def test_judge_without_web_writes_no_judge_events(tmp_path, monkeypatch):
    bundle = _write_min_bundle(tmp_path)
    monkeypatch.setattr("ramcheck.cli.OpenAIJudgeBackend", _FakeJudgeBackend)
    result = typer.testing.CliRunner().invoke(
        cli.app,
        ["judge", "--bundle", str(bundle), "--judge-config", str(bundle / "judge.yaml")],
    )
    assert result.exit_code == 0, result.output
    assert (bundle / "scorecard.md").exists()
    assert not (bundle / "judge_events.jsonl").exists()  # no --web → no event file


def _patch_monitor(monkeypatch):
    @contextlib.contextmanager
    def _fake_live_monitor(*args, **kwargs):
        yield (None, None)

    monkeypatch.setattr("ramcheck.cli._live_monitor", _fake_live_monitor)
    monkeypatch.setattr("ramcheck.cli._hold_monitor", lambda *a, **k: None)
    monkeypatch.setattr("ramcheck.cli.OpenAIJudgeBackend", _FakeJudgeBackend)


def test_judge_web_writes_full_event_stream(tmp_path, monkeypatch):
    bundle = _write_min_bundle(tmp_path)
    _patch_monitor(monkeypatch)
    result = typer.testing.CliRunner().invoke(
        cli.app,
        [
            "judge",
            "--bundle",
            str(bundle),
            "--judge-config",
            str(bundle / "judge.yaml"),
            "--web",
            "--no-open",
        ],
    )
    assert result.exit_code == 0, result.output
    types = [
        json.loads(ln)["type"]
        for ln in (bundle / "judge_events.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert types[0] == "judge_start"
    assert "verdict" in types and "master" in types
    assert types[-1] == "judge_done"
    assert (bundle / "scorecard.md").exists()


def test_judge_model_override_replaces_config_model():
    from ramcheck.judge import JudgeConfig, JudgeEndpoint

    jc = JudgeConfig(endpoint=JudgeEndpoint(base_url="http://x/v1"), model="qwen", temperature=0.0)

    # mirrors the cli `judge` override: apply only when --judge-model is non-empty
    def _apply(jc, judge_model):
        return jc.model_copy(update={"model": judge_model.strip()}) if judge_model.strip() else jc

    overridden = _apply(jc, "gemma")
    assert overridden.model == "gemma"
    assert overridden.endpoint.base_url == "http://x/v1"  # endpoint untouched

    unchanged = _apply(jc, "")  # empty → config default, no override
    assert unchanged.model == "qwen"
    assert unchanged is jc


def test_judge_web_truncates_stale_events(tmp_path, monkeypatch):
    bundle = _write_min_bundle(tmp_path)
    _patch_monitor(monkeypatch)
    args = [
        "judge",
        "--bundle",
        str(bundle),
        "--judge-config",
        str(bundle / "judge.yaml"),
        "--web",
        "--no-open",
    ]
    runner = typer.testing.CliRunner()
    assert runner.invoke(cli.app, args).exit_code == 0
    assert runner.invoke(cli.app, args).exit_code == 0  # second --web run on the same bundle
    types = [
        json.loads(ln)["type"]
        for ln in (bundle / "judge_events.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert types.count("judge_start") == 1  # fresh stream, no stale accumulation
    assert types.count("judge_done") == 1
