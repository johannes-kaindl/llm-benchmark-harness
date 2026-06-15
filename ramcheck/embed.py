"""Embedding throughput (`ramcheck embed`).

RAG hangs on embedding throughput, not just chat, so this is a separate run:
N chunks of ~chunk_tokens against /v1/embeddings, measuring embeddings/s, total
time and peak RAM. The host sampler runs decoupled, same as the chat runner.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Protocol

from ramcheck import prompts as prompts_mod
from ramcheck.config import Config
from ramcheck.merge import aggregate_window, load_samples_jsonl
from ramcheck.models import EmbedResult
from ramcheck.sampler import HostSampler, read_power_source


class EmbedClient(Protocol):
    engine: str
    engine_version: str

    def embed(self, *, model: str, inputs: list[str]) -> int: ...


def make_chunks(num_chunks: int, chunk_tokens: int, counter: prompts_mod.TokenCounter) -> list[str]:
    """Deterministic corpus: num_chunks texts each padded to ~chunk_tokens."""
    base = prompts_mod.pad_to_tokens(chunk_tokens, counter)
    # Vary each chunk slightly so a server can't trivially cache identical inputs.
    return [f"[Dok {i}] {base}" for i in range(num_chunks)]


def run_embed(
    config: Config,
    client: EmbedClient,
    *,
    run_dir: Path,
    batch_size: int = 16,
) -> EmbedResult:
    run_dir.mkdir(parents=True, exist_ok=True)
    resources_path = run_dir / "resources_embed.jsonl"

    counter = prompts_mod.get_counter(config.embed.model)
    chunks = make_chunks(config.embed.num_chunks, config.embed.chunk_tokens, counter)

    stop = threading.Event()
    sampler = HostSampler(config.server_process_match, powermetrics=config.power_check)
    sampler_thread = threading.Thread(
        target=sampler.run_to_file, args=(resources_path, stop), daemon=True
    )
    sampler_thread.start()

    power_source = read_power_source()
    t0 = time.perf_counter()
    n_done = 0
    try:
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            n_done += client.embed(model=config.embed.model, inputs=batch)
    finally:
        total_s = time.perf_counter() - t0
        stop.set()
        sampler_thread.join(timeout=3)

    samples = load_samples_jsonl(resources_path)
    agg = aggregate_window(samples)
    eps = n_done / total_s if total_s > 0 else 0.0

    return EmbedResult(
        machine=config.machine,
        model=config.embed.model,
        engine=getattr(client, "engine", "openai-compat"),
        engine_version=getattr(client, "engine_version", "unknown"),
        num_chunks=n_done,
        chunk_tokens=config.embed.chunk_tokens,
        total_s=total_s,
        embeddings_per_s=eps,
        peak_rss_mb=agg.peak_rss_mb,
        sys_used_mb=agg.sys_used_mb,
        power_source=power_source,
        throttled=agg.throttled,
        seed=config.seed,
    )


def render_embed_md(result: EmbedResult, *, date_str: str) -> str:
    rss = f"{result.peak_rss_mb / 1024:.1f} GB" if result.peak_rss_mb else "—"
    return "\n".join(
        [
            "# ramcheck — Embedding-Durchsatz",
            "",
            f"> **Datum:** {date_str} · **Maschine:** {result.machine} · "
            f"**Engine:** {result.engine} {result.engine_version} · "
            f"**Power:** {result.power_source}",
            "",
            "| Maschine | Modell | N Chunks | Chunk-Tok | Embeddings/s | Gesamtzeit (s) | Peak-RAM |",
            "|---|---|---|---|---|---|---|",
            f"| {result.machine} | {result.model} | {result.num_chunks} | "
            f"{result.chunk_tokens} | {result.embeddings_per_s:.1f} | "
            f"{result.total_s:.1f} | {rss} |",
            "",
        ]
    )
