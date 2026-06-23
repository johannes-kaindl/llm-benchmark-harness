"""Single source of truth for what every metric/label in the GUI means.

Pure data — no GUI imports — so it is unit-testable and so a render-time test can
assert that every metric key a template shows is defined here (no unexplained
number can ship). The `metric()` Jinja macro pulls `short` for tooltips.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Glossary:
    term: str  # human label
    short: str  # one-line tooltip
    long: str  # full explanation (glossary page / explainer)


GLOSSARY: dict[str, Glossary] = {
    "quality_pct": Glossary(
        "Qualität %",
        "Σ(Score × Gewicht) / Max × 100 über die holistisch bewerteten Dimensionen.",
        "Der Judge vergibt pro Master-Dimension 1–5; gewichtet, normiert auf 0–100 %. "
        "Belegt durch eine Begründung mit zitierten Prompt-IDs.",
    ),
    "ttft_p50": Glossary(
        "TTFT P50",
        "Time-To-First-Token, Median (s): Wartezeit bis zum ersten sichtbaren Antwort-Token.",
        "Reasoning-Tokens zählen NICHT zur TTFT — erst der erste Content-Token stoppt die Uhr.",
    ),
    "decode_median": Glossary(
        "Decode Median",
        "Median der Decode-Geschwindigkeit (Token/s) im Antwort-Fenster nach der TTFT.",
        "completion_tokens / (e2e − ttft). Reine Generier-Rate, ohne Prefill.",
    ),
    "prefill_tps": Glossary(
        "Prefill",
        "Prompt-Verarbeitung (Token/s): prompt_tokens / TTFT.",
        "Wie schnell der Prompt eingelesen wird, bevor das erste Token kommt.",
    ),
    "e2e": Glossary(
        "Gesamtzeit",
        "End-to-End-Wandzeit einer Antwort (s), von Absenden bis letztem Token.",
        "Enthält Prefill + Thinking + Decode.",
    ),
    "total_throughput": Glossary(
        "Gesamt-Durchsatz",
        "(prompt_tokens + completion_tokens) / Gesamtzeit (Token/s).",
        "Effektiver Durchsatz inkl. Prompt-Verarbeitung — näher am gefühlten Tempo als Decode allein.",
    ),
    "system_peak_ram": Glossary(
        "System-Peak",
        "Höchster System-Speicher während des Laufs (GB) — OS + alle Prozesse, nicht nur das Modell.",
        "Auf Apple-Silicon mmap't mlx die Gewichte in den Unified Memory; RSS unterzählt. "
        "Diese Zahl ist NICHT maschinen-vergleichbar — siehe Modell-Delta.",
    ),
    "model_delta_ram": Glossary(
        "Modell-Delta",
        "System-Peak minus Baseline vor dem Lauf (GB) — der maschinen-vergleichbare Speicher-Zuwachs.",
        "Baseline = Systemspeicher unmittelbar vor der ersten Anfrage. Der Kontext-/KV-Anteil "
        "ist im Unified Memory nicht sauber von den Gewichten trennbar (dokumentierte Unschärfe).",
    ),
    "mem_pressure": Glossary(
        "Memory-Pressure",
        "macOS-Speicherdruck (normal/warn/critical) im Lauf-Fenster.",
        "Aus `memory_pressure`. Steigt, wenn das System komprimiert/auslagert.",
    ),
    "variant_baseline": Glossary(
        "baseline",
        "Lauf MIT dem im Pack definierten System-Prompt.",
        "Die »baseline«-Variante setzt den Einsatzzweck-Prompt; Vergleich gegen »none« misst dessen Effekt.",
    ),
    "variant_none": Glossary(
        "none",
        "Lauf OHNE System-Prompt (Kontrollgruppe).",
        "Misst das nackte Modell; Differenz zu »baseline« = Wirkung des Prompts.",
    ),
    "reasoning_only": Glossary(
        "reasoning-only",
        "Modell schrieb nur ins Reasoning-Feld, keine sichtbare Antwort → aus dem Mittel ausgenommen.",
        "Setup-Hinweis (z. B. Token-Budget zu klein), kein inhaltliches Urteil. Echt leer bleibt 1.",
    ),
    "reasoning_duration": Glossary(
        "Thinking-Dauer",
        "Zeit im Reasoning-Kanal (s), bevor der erste Content-Token kommt.",
        "Getrennt von der Antwortzeit gemessen.",
    ),
    "reasoning_tps": Glossary(
        "Thinking-Tempo",
        "Reasoning-Token/s (heuristisch gezählt).",
        "Die API liefert nur eine aggregierte completion_tokens-Zahl; Reasoning-Tokens werden geschätzt.",
    ),
    "cold_start": Glossary(
        "Cold-Start",
        "Allererste Anfrage des Laufs — separat ausgewiesen, nie in den Aggregaten.",
        "Misst »wird das Modell warm«; aus Mittelwerten ausgeschlossen.",
    ),
    "cpu": Glossary(
        "CPU Ø/Max",
        "System-CPU-Last (%) im Antwort-Fenster, Durchschnitt/Maximum.",
        "»n. v.«, wenn das Bundle vor der CPU-Erfassung lief.",
    ),
}


def describe(key: str) -> Glossary:
    """Glossary entry for a key, or a safe placeholder for an unknown key."""
    return GLOSSARY.get(key, Glossary(term=key, short="", long=""))
