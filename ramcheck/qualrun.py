"""Qualitative run: drive a pack's prompts through the models, capture answers + perf.

The deterministic half of the eval. Reuses the latency runner's ``stream_once``
(so every answer comes with the same TTFT/decode/prefill derivation as a chat run)
and the decoupled host sampler + merge (so peak system RAM / pressure / throttle
land per request). The matrix is model × prompt-variant × pack-prompt × repeat.

Persistence is **incremental and resumable**: each answer is appended to
``responses.jsonl`` the moment its cell finishes (so an interruption keeps the
done work), and ``--resume`` skips the cells already on disk. Resources are merged
in a finalize pass (the expensive part — generation — is what must survive a crash).
"""

from __future__ import annotations

import csv
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ramcheck import merge as merge_mod
from ramcheck import prompts as prompts_mod
from ramcheck.config import Config, ModelSpec
from ramcheck.models import RAW_CSV_COLUMNS
from ramcheck.pack import Category, Pack, PackPrompt, PromptVariant
from ramcheck.preflight import PreflightResult, preflight_models
from ramcheck.results import EvalResponse
from ramcheck.runner import Sampler, StreamClient, derive_rates, resolve_engine, stream_once

EvalKey = tuple[str, str, str, int]  # (model, variant, prompt_id, repeat)


@dataclass
class EvalCell:
    model: ModelSpec
    variant: PromptVariant
    category: Category
    prompt: PackPrompt
    repeat: int


def iter_eval_cells(config: Config, pack: Pack) -> list[EvalCell]:
    """Every (model × variant × prompt × repeat) cell, in a stable order."""
    cells: list[EvalCell] = []
    for model in config.models:
        for variant in pack.prompt_variants:
            for category, prompt in pack.all_prompts():
                for repeat in range(prompt.repeats):
                    cells.append(EvalCell(model, variant, category, prompt, repeat))
    return cells


def _messages(variant: PromptVariant, prompt: PackPrompt) -> list[dict[str, object]]:
    msgs: list[dict[str, object]] = []
    if variant.system_prompt:
        msgs.append({"role": "system", "content": variant.system_prompt})
    msgs.append({"role": "user", "content": prompt.prompt})
    return msgs


def _resp_key(r: EvalResponse) -> EvalKey:
    return (r.model, r.variant, r.prompt_id, r.repeat)


def _cell_key(cell: EvalCell) -> EvalKey:
    return (cell.model.id, cell.variant.id, cell.prompt.id, cell.repeat)


def run_eval(
    config: Config,
    pack: Pack,
    client: StreamClient,
    *,
    run_dir: str | Path,
    sampler: Sampler | None = None,
    settle_s: float = 0.0,
    resume: bool = False,
    on_run_start: Callable[[int], None] | None = None,
    on_cell_start: Callable[[int, EvalCell], None] | None = None,
    on_cell_done: Callable[[int, EvalResponse], None] | None = None,
    on_preflight: Callable[[list[PreflightResult]], None] | None = None,
    strict_preflight: bool = False,
) -> list[EvalResponse]:
    """Drive the eval matrix; append answers incrementally; resume skips done cells."""
    from ramcheck.runner import _SamplerProcess
    from ramcheck.sampler import read_power_source

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    resources_path = run_dir / "resources.jsonl"
    responses_path = run_dir / "responses.jsonl"

    engine = config.engine or resolve_engine(config.endpoint.base_url)
    engine_version = config.engine_version or getattr(client, "engine_version", "") or "unknown"
    power_source = read_power_source()

    prior = load_responses_jsonl(responses_path) if (resume and responses_path.exists()) else []
    done_keys = {_resp_key(r) for r in prior}

    if sampler is None:
        sampler = _SamplerProcess(
            resources_path, config.server_process_match, powermetrics=config.power_check
        )

    counters: dict[str, prompts_mod.TokenCounter] = {}

    def counter_for(model_id: str) -> prompts_mod.TokenCounter:
        if model_id not in counters:
            counters[model_id] = prompts_mod.get_counter(model_id)
        return counters[model_id]

    cells = iter_eval_cells(config, pack)
    if not resume:
        max_prompt_budget = max((p.max_tokens for _, p in pack.all_prompts()), default=0)

        def _budget_for(m: ModelSpec) -> int:
            return max_prompt_budget + m.reasoning_headroom_tokens

        pf = preflight_models(client, config.models, budget_for=_budget_for)
        if on_preflight is not None:
            on_preflight(pf)
        bad = [r for r in pf if r.status != "ok"]
        if strict_preflight and bad:
            raise RuntimeError(
                "Pre-Flight: " + "; ".join(f"{r.model} → {r.status} ({r.detail})" for r in bad)
            )
    if on_run_start is not None:
        on_run_start(len(cells))
    sampler.start()
    time.sleep(settle_s)

    new: list[EvalResponse] = []
    cold_seen = bool(prior)  # cold-start belongs to the very first request of the whole run
    mode = "a" if prior else "w"
    try:
        with responses_path.open(mode, encoding="utf-8") as fh:
            for i, cell in enumerate(cells):
                if _cell_key(cell) in done_keys:
                    continue
                if on_cell_start is not None:
                    on_cell_start(i, cell)
                outcome = stream_once(
                    client,
                    messages=_messages(cell.variant, cell.prompt),
                    model=cell.model.id,
                    max_tokens=cell.prompt.max_tokens + cell.model.reasoning_headroom_tokens,
                    temperature=pack.sampling.temperature,
                    seed=pack.sampling.seed,
                    counter=counter_for(cell.model.id),
                    extra_body=cell.model.extra_body or None,
                )
                prefill, decode = derive_rates(outcome)
                is_cold = not cold_seen
                cold_seen = True
                content_empty = outcome.ok and not outcome.text.strip()
                resp = EvalResponse(
                    pack_id=pack.id,
                    pack_version=pack.version,
                    machine=config.machine,
                    model=cell.model.id,
                    quant=cell.model.quant,
                    engine=engine,
                    engine_version=engine_version,
                    variant=cell.variant.id,
                    category=cell.category.id,
                    prompt_id=cell.prompt.id,
                    repeat=cell.repeat,
                    response_text=outcome.text,
                    content_empty=content_empty,
                    ttft_s=outcome.ttft_s,
                    decode_tps=decode,
                    prefill_tps=prefill,
                    e2e_s=outcome.e2e_s,
                    prompt_tokens=outcome.prompt_tokens,
                    completion_tokens=outcome.completion_tokens,
                    is_cold_start=is_cold,
                    power_source=power_source,
                    peak_rss_mb=None,
                    sys_used_mb=None,
                    mem_pressure_max="",
                    throttled=False,
                    ok=outcome.ok,
                    error=outcome.error,
                    seed=pack.sampling.seed,
                    t_start=outcome.t_start,
                    t_end=outcome.t_end,
                    reasoning_chars=len(outcome.reasoning_text),
                    reasoning_text=outcome.reasoning_text if content_empty else "",
                )
                # Persist immediately so an interruption keeps every finished answer.
                fh.write(json.dumps(resp.as_dict(), ensure_ascii=False) + "\n")
                fh.flush()
                new.append(resp)
                if on_cell_done is not None:
                    on_cell_done(i, resp)
    finally:
        sampler.stop()

    # Finalize: merge host resources into this run's new responses by time window.
    samples = merge_mod.load_samples_jsonl(resources_path)
    for r in new:
        agg = merge_mod.resources_for_window(samples, r.t_start, r.t_end)
        r.peak_rss_mb = agg.peak_rss_mb
        r.sys_used_mb = agg.sys_used_mb
        r.mem_pressure_max = agg.mem_pressure_max
        r.throttled = r.throttled or agg.throttled

    all_responses = prior + new
    _write_responses(responses_path, all_responses)  # clean rewrite with resources filled
    _write_perf_csv(run_dir / "perf.csv", all_responses)
    return all_responses


def _write_responses(path: Path, responses: list[EvalResponse]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in responses:
            fh.write(json.dumps(r.as_dict(), ensure_ascii=False) + "\n")


def _resp_to_raw_row(r: EvalResponse) -> dict[str, object]:
    return {
        "run_id": f"{r.model}|{r.variant}|{r.prompt_id}|{r.repeat}",
        "machine": r.machine,
        "model": r.model,
        "quant": r.quant,
        "engine": r.engine,
        "engine_version": r.engine_version,
        "scenario": r.prompt_id,
        "target_ctx": 0,
        "actual_prompt_tokens": r.prompt_tokens,
        "completion_tokens": r.completion_tokens,
        "ttft_s": r.ttft_s,
        "decode_tps": r.decode_tps,
        "prefill_tps": r.prefill_tps,
        "e2e_s": r.e2e_s,
        "peak_rss_mb": r.peak_rss_mb,
        "sys_used_mb": r.sys_used_mb,
        "swap_delta_mb": 0.0,
        "mem_pressure_max": r.mem_pressure_max,
        "throttled": r.throttled,
        "power_source": r.power_source,
        "seed": r.seed,
        "warmup": False,
        "is_cold_start": r.is_cold_start,
        "ok": r.ok,
        "error": r.error,
    }


def _write_perf_csv(path: Path, responses: list[EvalResponse]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RAW_CSV_COLUMNS)
        writer.writeheader()
        for r in responses:
            writer.writerow(_resp_to_raw_row(r))


def load_responses_jsonl(path: str | Path) -> list[EvalResponse]:
    """Read responses.jsonl back into EvalResponse records, tolerant of a half-written
    final line (an interrupted append) — that line is skipped and its cell re-runs."""
    out: list[EvalResponse] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(EvalResponse(**json.loads(line)))
        except Exception:
            continue
    return out
