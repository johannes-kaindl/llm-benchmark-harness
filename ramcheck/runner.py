"""Latency runner: OpenAI-compatible streaming + TTFT/prefill/decode derivation.

Metric derivation, exactly as specified:
  * TTFT       = first non-empty streaming chunk − request send time
  * prefill_tps ≈ prompt_tokens / TTFT
  * decode_tps  = completion_tokens / (e2e − TTFT)
Token counts come from the `usage` field (stream_options include_usage). Host
sampling — not the request thread — remains the source of truth for memory, so
this module only times requests and records the wall-clock window for merge.py.
"""

from __future__ import annotations

import math
import select
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ramcheck import merge as merge_mod
from ramcheck import prompts as prompts_mod
from ramcheck.config import Config, ModelSpec
from ramcheck.models import RunRecord

# --- streaming client abstraction (injectable for tests) ---------------------


@dataclass
class StreamEvent:
    """One streamed chunk. Carries content text, reasoning ("thinking") text, or final usage."""

    delta_text: str = ""
    reasoning_text: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class StreamClient(Protocol):
    engine: str
    engine_version: str

    def stream(
        self,
        *,
        messages: list[dict[str, object]],
        model: str,
        max_tokens: int | None,
        temperature: float,
        seed: int,
        extra_body: dict[str, object] | None = None,
    ) -> Iterator[StreamEvent]: ...


class Sampler(Protocol):
    """Anything the runner can start before the matrix and stop after it."""

    def start(self) -> None: ...
    def stop(self) -> None: ...


@dataclass
class RequestOutcome:
    ttft_s: float
    e2e_s: float
    prompt_tokens: int
    completion_tokens: int
    t_start: float
    t_end: float
    text: str = ""
    reasoning_text: str = ""  # "thinking" tokens (separate channel; not counted toward TTFT)
    reasoning_duration_s: float = math.nan  # time spent in the reasoning channel (s); nan if none
    reasoning_completion_tokens: int = 0  # heuristic reasoning-token count
    reasoning_tps: float = math.nan  # reasoning tokens / reasoning_duration_s; nan-safe
    ok: bool = True
    error: str = ""


def resolve_engine(base_url: str) -> str:
    if ":1234" in base_url:
        return "lm-studio"
    if ":8080" in base_url or ":8000" in base_url:
        return "mlx"
    return "openai-compat"


def stream_once(
    client: StreamClient,
    *,
    messages: list[dict[str, object]],
    model: str,
    max_tokens: int | None,
    temperature: float,
    seed: int,
    extra_body: dict[str, object] | None = None,
    counter: prompts_mod.TokenCounter | None = None,
    clock: Callable[[], float] = time.perf_counter,
    wall: Callable[[], float] = time.time,
) -> RequestOutcome:
    """Send one streaming request and derive the latency metrics.

    `clock`/`wall` are injectable so tests can drive deterministic timings.
    If the server omits `usage` despite include_usage, completion tokens fall
    back to a heuristic over the accumulated text (flagged only by being approximate).
    """
    t0 = clock()
    wall_start = wall()
    ttft: float | None = None
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    t_reasoning_start: float | None = None
    t_reasoning_last: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error = ""
    ok = True

    try:
        for ev in client.stream(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            **({"extra_body": extra_body} if extra_body else {}),
        ):
            if ev.delta_text:
                if ttft is None:
                    ttft = clock() - t0
                text_parts.append(ev.delta_text)
            if ev.reasoning_text:
                t_reasoning = clock() - t0
                if t_reasoning_start is None:
                    t_reasoning_start = t_reasoning
                t_reasoning_last = t_reasoning
                reasoning_parts.append(ev.reasoning_text)
            if ev.prompt_tokens is not None:
                prompt_tokens = ev.prompt_tokens
            if ev.completion_tokens is not None:
                completion_tokens = ev.completion_tokens
    except Exception as exc:
        ok = False
        error = f"{type(exc).__name__}: {exc}"

    e2e = clock() - t0
    wall_end = wall()
    text = "".join(text_parts)
    reasoning = "".join(reasoning_parts)

    if ttft is None:
        ttft = math.nan
    if completion_tokens is None:
        c = counter or prompts_mod.HeuristicCounter()
        completion_tokens = c.count(text) if text else 0
    if prompt_tokens is None:
        prompt_tokens = 0

    if t_reasoning_start is not None and t_reasoning_last is not None:
        reasoning_duration_s = t_reasoning_last - t_reasoning_start
    else:
        reasoning_duration_s = math.nan
    reasoning_completion_tokens = (
        (counter or prompts_mod.HeuristicCounter()).count(reasoning) if reasoning else 0
    )
    reasoning_tps = (
        reasoning_completion_tokens / reasoning_duration_s
        if not math.isnan(reasoning_duration_s) and reasoning_duration_s > 0
        else math.nan
    )

    return RequestOutcome(
        ttft_s=ttft,
        e2e_s=e2e,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        t_start=wall_start,
        t_end=wall_end,
        text=text,
        reasoning_text=reasoning,
        reasoning_duration_s=reasoning_duration_s,
        reasoning_completion_tokens=reasoning_completion_tokens,
        reasoning_tps=reasoning_tps,
        ok=ok,
        error=error,
    )


def derive_rates(outcome: RequestOutcome) -> tuple[float, float]:
    """(prefill_tps, decode_tps) from an outcome. nan-safe."""
    prefill = (
        outcome.prompt_tokens / outcome.ttft_s
        if outcome.ttft_s and not math.isnan(outcome.ttft_s) and outcome.ttft_s > 0
        else math.nan
    )
    decode_window = outcome.e2e_s - outcome.ttft_s
    decode = (
        outcome.completion_tokens / decode_window
        if not math.isnan(decode_window) and decode_window > 0
        else math.nan
    )
    return prefill, decode


# --- cell planning -----------------------------------------------------------

CONTEXT_SENSITIVE = {"rag_synth", "longctx_stress"}


@dataclass
class Cell:
    model: ModelSpec
    scenario: str
    target_ctx: int  # 0 = native prompt size (context-free scenario)
    max_tokens: int


def iter_cells(config: Config) -> list[Cell]:
    """Every (model × scenario × bucket) cell. Context-free scenarios get one cell."""
    cells: list[Cell] = []
    for model in config.models:
        for scenario in config.scenarios:
            buckets = config.context_buckets if scenario in CONTEXT_SENSITIVE else [0]
            for ctx in buckets:
                cells.append(
                    Cell(
                        model=model,
                        scenario=scenario,
                        target_ctx=ctx,
                        max_tokens=config.max_tokens_for(scenario, model),
                    )
                )
    return cells


# --- benchmark orchestration -------------------------------------------------

ClientFactory = object  # callable: (Config) -> StreamClient


@dataclass
class _RunContext:
    config: Config
    client: StreamClient
    counters: dict[str, prompts_mod.TokenCounter] = field(default_factory=dict)

    def counter_for(self, model_id: str) -> prompts_mod.TokenCounter:
        if model_id not in self.counters:
            self.counters[model_id] = prompts_mod.get_counter(model_id)
        return self.counters[model_id]


def _make_record(
    config: Config,
    cell: Cell,
    outcome: RequestOutcome,
    *,
    engine: str,
    engine_version: str,
    power_source: str,
    run_id: str,
    warmup: bool,
    is_cold_start: bool,
) -> RunRecord:
    prefill, decode = derive_rates(outcome)
    return RunRecord(
        run_id=run_id,
        machine=config.machine,
        model=cell.model.id,
        quant=cell.model.quant,
        engine=engine,
        engine_version=engine_version,
        scenario=cell.scenario,
        target_ctx=cell.target_ctx,
        seed=config.seed,
        power_source=power_source,
        actual_prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        ttft_s=outcome.ttft_s,
        decode_tps=decode,
        prefill_tps=prefill,
        e2e_s=outcome.e2e_s,
        t_start=outcome.t_start,
        t_end=outcome.t_end,
        warmup=warmup,
        is_cold_start=is_cold_start,
        ok=outcome.ok,
        error=outcome.error,
    )


def _build_messages(ctx: _RunContext, cell: Cell) -> list[dict[str, object]]:
    counter = ctx.counter_for(cell.model.id)
    return prompts_mod.build_messages(
        cell.scenario,
        target_ctx=cell.target_ctx,
        counter=counter,
        variant_index=0,
        vlm_image_path=ctx.config.vlm.image_path if cell.scenario == "vlm" else None,
    )


class _SamplerProcess:
    """Decoupled host sampler as its own process (brief: two decoupled processes)."""

    def __init__(self, out_path: Path, server_match: str, powermetrics: bool) -> None:
        self.out_path = out_path
        self.server_match = server_match
        self.powermetrics = powermetrics
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread_stop: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        cmd = [
            sys.executable,
            "-m",
            "ramcheck.sampler",
            "--out",
            str(self.out_path),
            "--server-match",
            self.server_match,
        ]
        if not self.powermetrics:
            cmd.append("--no-powermetrics")
        try:
            self._proc = subprocess.Popen(cmd)
        except Exception:
            self._start_thread()

    def _start_thread(self) -> None:
        from ramcheck.sampler import HostSampler

        self._thread_stop = threading.Event()
        sampler = HostSampler(self.server_match, powermetrics=self.powermetrics)
        self._thread = threading.Thread(
            target=sampler.run_to_file, args=(self.out_path, self._thread_stop), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._thread_stop is not None:
            self._thread_stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)


class _WebMonitorProcess:
    """Decoupled live monitor as its own process (mirrors _SamplerProcess).

    No thread fallback on purpose: a monitor that can't spawn must NOT run inside the
    measurement process — that would defeat the decoupling. It just fails to start.
    """

    def __init__(
        self, bundle: Path, port: int = 0, events_name: str = "events.jsonl", view: str = "eval"
    ) -> None:
        self.bundle = bundle
        self.port = port
        self.events_name = events_name
        self.view = view
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> int | None:
        """Spawn the monitor; return the bound port (read from its stdout), or None."""
        cmd = [
            sys.executable,
            "-m",
            "ramcheck.webmon",
            "--bundle",
            str(self.bundle),
            "--port",
            str(self.port),
            "--events",
            self.events_name,
            "--view",
            self.view,
        ]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        except Exception:
            return None
        stdout = self._proc.stdout
        if stdout is None:
            return None
        try:
            # Bounded wait: if the child never reports a port (hung/crashed), don't block
            # the whole eval. Close the read end after — the child only prints once.
            ready, _, _ = select.select([stdout], [], [], 10.0)
            if not ready:
                self._proc.kill()
                return None
            line = stdout.readline().decode("utf-8").strip()
        finally:
            stdout.close()
        try:
            return int(line)
        except ValueError:
            return None

    def wait(self) -> None:
        """Block until the monitor process exits (i.e. until the user presses Ctrl-C)."""
        if self._proc is not None:
            self._proc.wait()

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def run_benchmark(
    config: Config,
    client: StreamClient,
    *,
    run_dir: Path,
    settle_s: float = 0.0,
    sampler: Sampler | None = None,
    on_run_start: Callable[[int], None] | None = None,
    on_cell_start: Callable[[int, Cell], None] | None = None,
    on_cell_done: Callable[[int, RunRecord], None] | None = None,
) -> list[RunRecord]:
    """Drive the full matrix and return merged records.

    One warmup per cell is discarded (kept raw, flagged). A single cold-start TTFT
    is measured once at the very start. The host sampler runs decoupled (own process
    by default; injectable for tests) and is merged in by time window afterwards.
    """
    from ramcheck.sampler import read_power_source

    run_dir.mkdir(parents=True, exist_ok=True)
    resources_path = run_dir / "resources.jsonl"

    engine = config.engine or resolve_engine(config.endpoint.base_url)
    engine_version = config.engine_version or getattr(client, "engine_version", "") or "unknown"
    power_source = read_power_source()

    ctx = _RunContext(config=config, client=client)
    if sampler is None:
        sampler = _SamplerProcess(
            resources_path,
            config.server_process_match,
            powermetrics=config.power_check,
        )
    sampler.start()
    time.sleep(settle_s)

    records: list[RunRecord] = []
    cells = iter_cells(config)
    if on_run_start is not None:
        on_run_start(len(cells))
    cold_done = False

    try:
        for ci, cell in enumerate(cells):
            if on_cell_start is not None:
                on_cell_start(ci, cell)
            messages = _build_messages(ctx, cell)
            counter = ctx.counter_for(cell.model.id)

            # Cold-start: the very first request overall, reported separately.
            if not cold_done:
                outcome = stream_once(
                    client,
                    messages=messages,
                    model=cell.model.id,
                    max_tokens=cell.max_tokens,
                    temperature=config.temperature,
                    seed=config.seed,
                    counter=counter,
                )
                records.append(
                    _make_record(
                        config,
                        cell,
                        outcome,
                        engine=engine,
                        engine_version=engine_version,
                        power_source=power_source,
                        run_id=f"cold-{ci}",
                        warmup=False,
                        is_cold_start=True,
                    )
                )
                cold_done = True

            # Warmup (discarded) + N measured runs.
            for ri in range(config.runs_per_cell):
                outcome = stream_once(
                    client,
                    messages=messages,
                    model=cell.model.id,
                    max_tokens=cell.max_tokens,
                    temperature=config.temperature,
                    seed=config.seed,
                    counter=counter,
                )
                records.append(
                    _make_record(
                        config,
                        cell,
                        outcome,
                        engine=engine,
                        engine_version=engine_version,
                        power_source=power_source,
                        run_id=f"{ci}-{ri}",
                        warmup=(ri == 0),
                        is_cold_start=False,
                    )
                )
            if on_cell_done is not None and records:
                on_cell_done(ci, records[-1])
    finally:
        sampler.stop()

    samples = merge_mod.load_samples_jsonl(resources_path)
    return merge_mod.merge(records, samples)
