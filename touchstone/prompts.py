"""Prompt set + deterministic token padding to the context buckets.

Tokenizers differ per model, so we pad toward a *target* token count but always
report against the real ``usage.prompt_tokens`` later (see report.py). Padding is
deterministic (no RNG) so two runs of the same cell send byte-identical prompts.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Repo-level prompts/ dir (sibling of the package). Files override the embedded text.
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Heuristic fallback: average characters per token for German text. Corrected
# after the fact against usage.prompt_tokens, so a rough value is fine.
CHARS_PER_TOKEN_DE = 4.0

# Embedded base text so the package works without the prompts/ files present.
_EMBEDDED_BASE_DE = (
    "Die lokale Inferenz auf Apple-Silicon verschiebt das Engpass-Profil weg von "
    "der reinen Rechenleistung hin zum vereinheitlichten Speicher. Sobald das Modell "
    "samt Kontext nicht mehr vollständig in den physischen Arbeitsspeicher passt, "
    "beginnt der Kernel mit Kompression und Auslagerung, der Speicherdruck steigt, "
    "und die Latenz wird sprunghaft unvorhersehbar. Genau diese Schwelle soll der "
    "Benchmark sichtbar machen: nicht der Mittelwert zählt, sondern die Verteilung "
    "und der Moment, in dem die Maschine unter Last die Konsistenz verliert. "
)

_BODYDOUBLE_VARIANTS = [
    "Zerlege die Aufgabe „Wochenplanung erstellen“ in drei erste, konkrete Schritte.",
    "Zerlege die Aufgabe „Ablage digitalisieren“ in drei erste, konkrete Schritte.",
    "Zerlege die Aufgabe „Vortrag vorbereiten“ in drei erste, konkrete Schritte.",
    "Zerlege die Aufgabe „Umzug organisieren“ in drei erste, konkrete Schritte.",
    "Zerlege die Aufgabe „Steuerunterlagen sortieren“ in drei erste, konkrete Schritte.",
]

_COMPOSE_PARAGRAPH = (
    "Notiz: Das Test-Setup vergleicht zwei Maschinen unter identischer Last. "
    "Die kleine Maschine hat knappen Speicher, die große reichlich; beide laufen am Netz. "
    "Gemessen werden Reaktionszeit, Durchsatz und der Speicherdruck unter wachsendem Kontext. "
    "Ziel ist eine belastbare Entscheidung, welche Maschine den Alltag besser trägt."
)


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


@dataclass
class HeuristicCounter:
    """Char-per-token estimate. Cheap, deterministic, no model download."""

    chars_per_token: float = CHARS_PER_TOKEN_DE

    def count(self, text: str) -> int:
        return max(1, round(len(text) / self.chars_per_token))


class TransformersCounter:
    """Exact per-model token count via transformers AutoTokenizer (optional dep)."""

    def __init__(self, model_id: str) -> None:
        from transformers import AutoTokenizer  # imported lazily; optional extra

        self._tok = AutoTokenizer.from_pretrained(model_id)

    def count(self, text: str) -> int:
        return len(self._tok.encode(text))


def get_counter(model_id: str | None = None) -> TokenCounter:
    """Best tokenizer available: exact transformers counter, else heuristic."""
    if model_id:
        try:
            return TransformersCounter(model_id)
        except Exception:
            pass
    return HeuristicCounter()


def _read_base_text() -> str:
    f = PROMPTS_DIR / "base_de.md"
    if f.exists():
        text = f.read_text(encoding="utf-8").strip()
        if text:
            return text
    return _EMBEDDED_BASE_DE.strip()


def pad_to_tokens(target_tokens: int, counter: TokenCounter, base_text: str | None = None) -> str:
    """Deterministically build text of ~target_tokens by repeating and trimming base_text.

    Repeats the base paragraph (numbered as simulated source chunks) until the
    token target is met or exceeded, then trims word-by-word back under target.
    Never returns empty; tolerance is whatever a single word represents.
    """
    if target_tokens <= 0:
        return ""
    base = (base_text or _read_base_text()).strip()
    if not base:
        base = _EMBEDDED_BASE_DE.strip()

    parts: list[str] = []
    i = 0
    # Grow in chunk-sized blocks; each block is a labelled "source" for rag_synth.
    while counter.count("\n\n".join(parts)) < target_tokens:
        i += 1
        parts.append(f"[Quelle {i}] {base}")
        if i > 100_000:  # pragma: no cover - runaway guard
            break

    text = "\n\n".join(parts)
    # Trim back under target so we never overshoot the bucket badly.
    words = text.split(" ")
    while len(words) > 1 and counter.count(" ".join(words)) > target_tokens:
        words.pop()
    return " ".join(words)


# --- scenario builders -------------------------------------------------------

Message = dict[str, object]


def _user(text: str) -> list[Message]:
    return [{"role": "user", "content": text}]


def build_bodydouble(variant_index: int) -> list[Message]:
    task = _BODYDOUBLE_VARIANTS[variant_index % len(_BODYDOUBLE_VARIANTS)]
    return _user(task)


def build_compose() -> list[Message]:
    return _user(
        f"{_COMPOSE_PARAGRAPH}\n\n"
        "Schreibe diesen Absatz um und erweitere ihn auf etwa das Doppelte, "
        "in klarem, sachlichem Deutsch."
    )


def build_rag_synth(target_ctx: int, counter: TokenCounter) -> list[Message]:
    # Reserve a little headroom for the instruction so the whole prompt lands near target.
    body = pad_to_tokens(max(256, target_ctx - 64), counter)
    return _user(
        f"{body}\n\n"
        "Fasse die obigen Quellen in etwa fünf Sätzen zusammen und gib bei jeder "
        "Kernaussage die Quelle in eckigen Klammern an (z. B. [Quelle 3])."
    )


def build_longctx_stress(target_ctx: int, counter: TokenCounter) -> list[Message]:
    body = pad_to_tokens(max(256, target_ctx - 32), counter)
    return _user(f"{body}\n\nNenne in einem Satz das zentrale Thema der obigen Texte.")


def _image_data_url(image_path: str | Path) -> str:
    p = Path(image_path)
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    suffix = p.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    return f"data:image/{mime};base64,{data}"


def build_vlm(image_path: str | Path) -> list[Message]:
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Extrahiere den Text aus diesem Bild und fasse den Inhalt kurz zusammen.",
                },
                {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
            ],
        }
    ]


def build_messages(
    scenario: str,
    *,
    target_ctx: int,
    counter: TokenCounter,
    variant_index: int = 0,
    vlm_image_path: str | Path | None = None,
) -> list[Message]:
    """Dispatch to the right scenario builder. Raises for an unknown scenario."""
    if scenario == "bodydouble":
        return build_bodydouble(variant_index)
    if scenario == "compose":
        return build_compose()
    if scenario == "rag_synth":
        return build_rag_synth(target_ctx, counter)
    if scenario == "longctx_stress":
        return build_longctx_stress(target_ctx, counter)
    if scenario == "vlm":
        if not vlm_image_path:
            raise ValueError("vlm scenario requires vlm_image_path")
        return build_vlm(vlm_image_path)
    raise ValueError(f"unknown scenario: {scenario}")
