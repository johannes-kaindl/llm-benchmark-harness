import json

from fastapi.testclient import TestClient

from ramcheck.gui import app as gui_app
from ramcheck.gui.control import RunRegistry

PACK = "packs/ndassist.yaml"
DIMS = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]


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


def _resp_dict(model, variant, perf=None):
    from ramcheck.results import EvalResponse

    base = EvalResponse(
        pack_id="ndassist",
        pack_version=1,
        machine="t",
        model=model,
        quant="q",
        engine="e",
        engine_version="x",
        variant=variant,
        category="A",
        prompt_id="A1",
        repeat=0,
        response_text="ok",
        content_empty=False,
        ttft_s=0.1,
        decode_tps=1.0,
        prefill_tps=1.0,
        e2e_s=1.0,
        prompt_tokens=1,
        completion_tokens=1,
        is_cold_start=False,
        power_source="ac",
        peak_rss_mb=0.0,
        sys_used_mb=0.0,
        mem_pressure_max="normal",
        throttled=False,
        ok=True,
        error="",
        seed=42,
        t_start=0.0,
        t_end=1.0,
        reasoning_chars=0,
    ).as_dict()
    if perf:
        base.update(perf)
    return base


def _write_bundle(d, *, groups, scores_by_group, blank_dims=(), perf=None):
    """Build a self-contained judged bundle under d using the real in-repo pack."""
    from ramcheck.pack import load_pack

    d.mkdir(parents=True, exist_ok=True)
    pk = load_pack(PACK)
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": pk.id,
                "pack_path": PACK,
                "models": [{"id": g[0], "quant": "q"} for g in groups],
                "date": "2026-06-20",
            }
        ),
        encoding="utf-8",
    )
    (d / "responses.jsonl").write_text(
        "\n".join(json.dumps(_resp_dict(m, v, perf)) for (m, v) in groups) + "\n",
        encoding="utf-8",
    )
    header = ["model", "variant", "metric_type", "metric", "weight", "score"]
    lines = [",".join(header)]
    for m, v in groups:
        scores = scores_by_group[(m, v)]
        for dim in pk.dimensions:
            raw = "" if dim.id in blank_dims else str(scores.get(dim.id, ""))
            lines.append(f"{m},{v},dimension,{dim.id},{dim.weight},{raw}")
    (d / "scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pk


def test_result_shows_per_answer_perf(tmp_path):
    d = tmp_path / "2026_eval_nd"
    _write_bundle(
        d,
        groups=[("m", "baseline")],
        scores_by_group={("m", "baseline"): {q: 4 for q in DIMS}},
        perf={
            "ttft_s": 0.2,
            "decode_tps": 30.0,
            "e2e_s": 1.5,
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    )
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert r.status_code == 200
    assert "tok/s" in r.text and "→" in r.text  # per-answer metric block rendered
    # scoped to the per-answer block: prompt→completion token arrow is unique to it
    # (the aggregate perf panel never renders the raw 100→50 token counts)
    assert "100→50 tok" in r.text


def test_per_answer_perf_suppresses_nan_metrics(tmp_path):
    # ttft/decode/prefill are genuinely math.nan when a request produced no content;
    # the `resp.x == resp.x` Jinja guard must keep "nan" out of the rendered metrics.
    d = tmp_path / "2026_eval_nan"
    _write_bundle(
        d,
        groups=[("m", "baseline")],
        scores_by_group={("m", "baseline"): {q: 4 for q in DIMS}},
        perf={
            "ttft_s": float("nan"),
            "decode_tps": float("nan"),
            "prefill_tps": float("nan"),
            "e2e_s": 1.0,
            "prompt_tokens": 10,
            "completion_tokens": 5,
        },
    )
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert r.status_code == 200
    # the NaN-valued metrics are not rendered at all (no "nan s" / "nan tok/s")
    assert "nan s" not in r.text and "nan tok/s" not in r.text
    # the still-valid metrics + token arrow remain
    assert "10→5 tok" in r.text


def test_per_answer_reasoning_block_renders_when_text_present(tmp_path):
    # reasoning_text is persisted only on content_empty; when present the collapsible
    # block (header + affordance + the text itself) must render.
    d = tmp_path / "2026_eval_reason"
    _write_bundle(
        d,
        groups=[("m", "baseline")],
        scores_by_group={("m", "baseline"): {q: 4 for q in DIMS}},
        perf={
            "content_empty": True,
            "response_text": "",
            "reasoning_chars": 28,
            "reasoning_text": "Schritt 1: nachdenken über A1.",
        },
    )
    r = _client(tmp_path).get(f"/result/{d.name}")
    assert r.status_code == 200
    assert "💭 Reasoning" in r.text  # block header
    assert "Reasoning anzeigen" in r.text  # collapsible affordance
    assert "Schritt 1: nachdenken über A1." in r.text  # the reasoning text itself
