"""Detect a stale Config.machine label that contradicts the detected hardware.

chip/ram_gb come from live hostinfo at eval time; machine is a hand-typed YAML
label. Reusing a config on new hardware without editing the label produces a row
where the detected truth and the label disagree. We never trust the label over
detected hardware — we flag the disagreement so the UI can demote it.
"""

from __future__ import annotations

import re

_GEN = re.compile(r"\bM(\d+)\b", re.IGNORECASE)  # M1, M2, ... chip generation


def label_mismatch(*, chip: str, ram_gb: str, machine: str) -> bool:
    """True when the machine label contradicts detected chip/ram (best-effort).

    Conservative: only flags a *positive* contradiction (label names a chip
    generation or RAM size that differs from detected). Empty/unknown values
    never flag — absence of a label is not a contradiction.
    """
    if not machine or not chip:
        return False
    det_gen = _GEN.search(chip)
    lab_gen = _GEN.search(machine)
    if det_gen and lab_gen and det_gen.group(1) != lab_gen.group(1):
        return True
    lab_ram = re.search(r"(\d+)\s*GB", machine, re.IGNORECASE)
    if lab_ram and ram_gb:
        try:
            if abs(float(lab_ram.group(1)) - float(ram_gb)) >= 1.0:
                return True
        except ValueError:
            return False
    return False
