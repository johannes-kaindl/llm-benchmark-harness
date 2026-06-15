"""Host metadata for the report header: macOS version, chip, RAM.

Best-effort and macOS-flavoured; every getter degrades to "unknown" off-mac so
the report still renders (e.g. when regenerating on a different machine).
"""

from __future__ import annotations

import platform
import subprocess

import psutil

MB = 1024 * 1024
GB = 1024 * MB


def macos_version() -> str:
    ver = platform.mac_ver()[0]
    return ver or "unknown"


def chip() -> str:
    try:
        out = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        return out or platform.processor() or "unknown"
    except Exception:
        return platform.processor() or "unknown"


def ram_gb() -> float:
    return round(psutil.virtual_memory().total / GB, 1)


def summary() -> dict[str, str]:
    return {
        "macos": macos_version(),
        "chip": chip(),
        "ram_gb": f"{ram_gb()} GB",
        "python": platform.python_version(),
    }
