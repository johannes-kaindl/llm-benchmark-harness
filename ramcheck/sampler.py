"""Host resource sampler — the part no off-the-shelf tool gives us.

Runs decoupled from the request path (own process: ``python -m ramcheck.sampler``)
and never estimates memory from the request thread. Samples at ~2 Hz, timestamped,
to a JSONL file that merge.py later joins against the latency log by time window.

Three macOS host signals are parsed; their parsers are pure and unit-tested:
  * powermetrics --samplers thermal → throttled?   (needs sudo)
  * memory_pressure                 → normal/warn/critical
  * pmset -g batt                   → ac/battery    (GPU throttles 30–50% on battery)
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

import psutil

from ramcheck.models import ResourceSample

MB = 1024 * 1024


# --- pure parsers ------------------------------------------------------------


def parse_pmset_power(text: str) -> str:
    """'Now drawing from ...' → ac | battery | unknown."""
    low = text.lower()
    if "battery power" in low:
        return "battery"
    if "ac power" in low:
        return "ac"
    return "unknown"


def parse_memory_pressure(text: str) -> str:
    """Map memory_pressure output to normal | warn | critical.

    Prefers an explicit level token if present; otherwise derives from the
    'System-wide memory free percentage' (>=30% normal, 10–30% warn, <10% critical).
    """
    low = text.lower()
    if "critical" in low:
        return "critical"
    if "warn" in low:  # matches "warn" and "warning"
        return "warn"
    m = re.search(r"free percentage:\s*([0-9]+(?:\.[0-9]+)?)\s*%", low)
    if m:
        free = float(m.group(1))
        if free < 10.0:
            return "critical"
        if free < 30.0:
            return "warn"
        return "normal"
    return "normal"


def parse_powermetrics_throttle(text: str) -> bool:
    """True if the thermal sampler block shows throttling.

    powermetrics output varies by macOS version, so we look defensively for any of:
      * an explicit 'Throttle: yes' (the brief's literal marker),
      * a thermal pressure level above nominal,
      * a CPU/GPU speed limit below 100%.
    """
    low = text.lower()
    if re.search(r"throttle:\s*yes", low):
        return True
    m = re.search(r"current pressure level:\s*([a-z]+)", low)
    if m and m.group(1) not in {"nominal", "normal"}:
        return True
    for sm in re.finditer(r"speed limit:\s*([0-9]+(?:\.[0-9]+)?)\s*%", low):
        if float(sm.group(1)) < 100.0:
            return True
    return False


def read_power_source() -> str:
    try:
        out = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5
        ).stdout
        return parse_pmset_power(out)
    except Exception:
        return "unknown"


def read_memory_pressure() -> str:
    try:
        out = subprocess.run(
            ["memory_pressure", "-Q"], capture_output=True, text=True, timeout=5
        ).stdout
        return parse_memory_pressure(out)
    except Exception:
        return "normal"


def find_server_pids(match: str) -> list[int]:
    """PIDs whose name or cmdline contains `match` (case-insensitive)."""
    if not match:
        return []
    needle = match.lower()
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if needle in name or needle in cmdline:
                pids.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def server_rss_mb(match: str) -> float | None:
    pids = find_server_pids(match)
    if not pids:
        return None
    total = 0
    for pid in pids:
        try:
            total += psutil.Process(pid).memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total / MB


# --- sampler -----------------------------------------------------------------


class _ThrottleWatcher:
    """Tails `sudo powermetrics --samplers thermal` and keeps the latest throttle flag."""

    def __init__(self, interval_ms: int = 1000) -> None:
        self.interval_ms = interval_ms
        self.throttled = False
        self.ok = False
        self._proc: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._buf: list[str] = []

    def start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                [
                    "sudo",
                    "-n",
                    "powermetrics",
                    "--samplers",
                    "thermal",
                    "-i",
                    str(self.interval_ms),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            self._proc = None
            return
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self.ok = True
            self._buf.append(line)
            if line.strip() == "" and self._buf:
                block = "".join(self._buf)
                self.throttled = parse_powermetrics_throttle(block)
                self._buf = []

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()


class HostSampler:
    def __init__(
        self,
        server_match: str = "",
        *,
        interval: float = 0.5,
        powermetrics: bool = True,
        pressure_every: float = 2.0,
    ) -> None:
        self.server_match = server_match
        self.interval = interval
        self.pressure_every = pressure_every
        self._watcher = _ThrottleWatcher() if powermetrics else None
        self._last_pressure = "normal"
        self._last_pressure_ts = 0.0

    def start(self) -> None:
        psutil.cpu_percent(interval=None)  # prime: first call returns 0.0, discard it
        if self._watcher is not None:
            self._watcher.start()

    def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()

    def _pressure(self, now: float) -> str:
        if now - self._last_pressure_ts >= self.pressure_every:
            self._last_pressure = read_memory_pressure()
            self._last_pressure_ts = now
        return self._last_pressure

    def sample_once(self) -> ResourceSample:
        now = time.time()
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return ResourceSample(
            ts=now,
            sys_used_mb=vm.used / MB,
            sys_available_mb=vm.available / MB,
            swap_used_mb=sw.used / MB,
            server_rss_mb=server_rss_mb(self.server_match),
            mem_pressure_level=self._pressure(now),
            throttled=self._watcher.throttled if self._watcher else False,
            cpu_pct=psutil.cpu_percent(interval=None),
        )

    def run_to_file(self, out_path: str | Path, stop_event: threading.Event) -> None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.start()
        try:
            with path.open("w", encoding="utf-8") as fh:
                while not stop_event.is_set():
                    sample = self.sample_once()
                    fh.write(json.dumps(asdict(sample)) + "\n")
                    fh.flush()
                    stop_event.wait(self.interval)
        finally:
            self.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ramcheck host resource sampler (standalone)")
    ap.add_argument("--out", required=True, help="JSONL output path")
    ap.add_argument("--server-match", default="", help="substring to match the server process")
    ap.add_argument(
        "--interval", type=float, default=0.5, help="sample interval seconds (2 Hz default)"
    )
    ap.add_argument("--no-powermetrics", action="store_true", help="skip thermal/throttle sampling")
    args = ap.parse_args(argv)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    sampler = HostSampler(
        args.server_match,
        interval=args.interval,
        powermetrics=not args.no_powermetrics,
    )
    sampler.run_to_file(args.out, stop)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
