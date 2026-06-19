"""Qualitative run: drive a pack's prompts through the models, capture answers + perf.

The deterministic half of the eval. Reuses the latency runner's ``stream_once``
(so every answer comes with the same TTFT/decode/prefill derivation as a chat run)
and the decoupled host sampler + merge (so peak system RAM / pressure / throttle
land per request). The matrix is model × prompt-variant × pack-prompt × repeat.
Output is the bundle: ``responses.jsonl`` (answers, judged later) + ``perf.csv``
(raw.csv-compatible, so ``ramcheck report`` works on eval runs too).
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path

from ramcheck import merge as merge_mod
from ramcheck import prompts as prompts_mod
from ramcheck.config import Config, ModelSpec
from ramcheck.models import RAW_CSV_COLUMNS, RunRecord
from ramcheck.pack import Category, Pack, PackPrompt, PromptVariant
from ramcheck.results import EvalResponse
from ramcheck.runner import Sampler, StreamClient, derive_rates, resolve_engine, stream_once


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


def run_eval(
    config: Config,
    pack: Pack,
    client: StreamClient,
    *,
    run_dir: str | Path,
    sampler: Sampler | None = None,
    settle_s: float = 0.0,
) -> list[EvalResponse]:
    """Drive the eval matrix, merge resources by time window, write the bundle."""
    from ramcheck.runner import _SamplerProcess
    from ramcheck.sampler import read_power_source

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    resources_path = run_dir / "resources.jsonl"

    engine = config.engine or resolve_engine(config.endpoint.base_url)
    engine_version = config.engine_version or getattr(client, "engine_version", "") or "unknown"
    power_source = read_power_source()

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
    sampler.start()
    time.sleep(settle_s)

    pairs: list[tuple[EvalResponse, RunRecord]] = []
    try:
        for ci, cell in enumerate(cells):
            outcome = stream_once(
                client,
                messages=_messages(cell.variant, cell.prompt),
                model=cell.model.id,
                max_tokens=cell.prompt.max_tokens,
                temperature=pack.sampling.temperature,
                seed=pack.sampling.seed,
                counter=counter_for(cell.model.id),
            )
            prefill, decode = derive_rates(outcome)
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
                is_cold_start=(ci == 0),
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
            )
            rec = RunRecord(
                run_id=f"{cell.model.id}|{cell.variant.id}|{cell.prompt.id}|{cell.repeat}",
                machine=config.machine,
                model=cell.model.id,
                quant=cell.model.quant,
                engine=engine,
                engine_version=engine_version,
                scenario=cell.prompt.id,
                target_ctx=0,
                seed=pack.sampling.seed,
                power_source=power_source,
                actual_prompt_tokens=outcome.prompt_tokens,
                completion_tokens=outcome.completion_tokens,
                ttft_s=outcome.ttft_s,
                decode_tps=decode,
                prefill_tps=prefill,
                e2e_s=outcome.e2e_s,
                t_start=outcome.t_start,
                t_end=outcome.t_end,
                warmup=False,
                is_cold_start=(ci == 0),
                ok=outcome.ok,
                error=outcome.error,
            )
            pairs.append((resp, rec))
    finally:
        sampler.stop()

    samples = merge_mod.load_samples_jsonl(resources_path)
    records = merge_mod.merge([rec for _, rec in pairs], samples)

    responses: list[EvalResponse] = []
    for (resp, _), rec in zip(pairs, records, strict=True):
        resp.peak_rss_mb = rec.peak_rss_mb
        resp.sys_used_mb = rec.sys_used_mb
        resp.mem_pressure_max = rec.mem_pressure_max
        resp.throttled = rec.throttled
        responses.append(resp)

    _write_responses(run_dir / "responses.jsonl", responses)
    _write_perf_csv(run_dir / "perf.csv", records)
    return responses


def _write_responses(path: Path, responses: list[EvalResponse]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in responses:
            fh.write(json.dumps(r.as_dict(), ensure_ascii=False) + "\n")


def _write_perf_csv(path: Path, records: list[RunRecord]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RAW_CSV_COLUMNS)
        writer.writeheader()
        for rec in records:
            d = rec.as_dict()
            writer.writerow({k: d[k] for k in RAW_CSV_COLUMNS})


def load_responses_jsonl(path: str | Path) -> list[EvalResponse]:
    """Read a bundle's responses.jsonl back into EvalResponse records (for judging)."""
    out: list[EvalResponse] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(EvalResponse(**json.loads(line)))
    return out
