"""Voice routing helpers for multi-character dubbing.

Users can mark dialogue in SRT with character labels, for example:
[boy] សួស្តី!
[girl] ចាស៎!

The parser strips the label from the spoken text and keeps the label metadata.
This service maps common labels to Khmer male/female neural voices. Unknown labels
fall back to the user's selected default voice so old SRT files still work.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from app.states import VOICE_LABELS

MALE_VOICE = "km-KH-PisethNeural"
FEMALE_VOICE = "km-KH-SreymomNeural"

MALE_LABELS = {
    "male",
    "man",
    "men",
    "boy",
    "father",
    "dad",
    "grandfather",
    "uncle",
    "brother",
    "husband",
    "he",
    "him",
    "m",
    "ប្រុស",
    "បុរស",
    "ក្មេងប្រុស",
    "បងប្រុស",
    "ឪពុក",
    "ពុក",
    "តា",
    "លោក",
}

FEMALE_LABELS = {
    "female",
    "woman",
    "women",
    "girl",
    "mother",
    "mom",
    "grandmother",
    "aunt",
    "sister",
    "wife",
    "she",
    "her",
    "f",
    "ស្រី",
    "នារី",
    "ក្មេងស្រី",
    "បងស្រី",
    "ម្ដាយ",
    "ម៉ាក់",
    "យាយ",
    "អ្នកស្រី",
}


def normalize_character_label(label: str | None) -> str:
    """Normalize a raw SRT character label for matching/display."""
    if not label:
        return ""
    return " ".join(label.strip().strip("[](){}:：-").split()).lower()


def voice_for_character(label: str | None, default_voice: str) -> str:
    """Return the voice to use for a subtitle character label."""
    normalized = normalize_character_label(label)
    if normalized in MALE_LABELS:
        return MALE_VOICE
    if normalized in FEMALE_LABELS:
        return FEMALE_VOICE
    return default_voice or FEMALE_VOICE


def voice_display_name(voice: str) -> str:
    return VOICE_LABELS.get(voice, voice or "Default")


def summarize_character_voices(items: Iterable[object], default_voice: str) -> list[str]:
    """Build short preview lines for detected SRT character labels."""
    counter: Counter[tuple[str, str]] = Counter()
    for item in items:
        label = getattr(item, "character_label", None)
        if not label:
            continue
        voice = voice_for_character(label, default_voice)
        counter[(str(label), voice)] += 1
    lines: list[str] = []
    for (label, voice), count in counter.most_common(8):
        lines.append(f"• {label}: {voice_display_name(voice)} ({count} បន្ទាត់)")
    return lines
