"""Text helpers."""

from __future__ import annotations

import re


def normalize_tts_text(text: str) -> str:
    """Clean subtitle text before sending it to TTS."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate(text: str, max_len: int = 250) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
