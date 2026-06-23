"""Text helpers."""

from __future__ import annotations

import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def normalize_tts_text(text: str) -> str:
    """Clean subtitle text before sending it to TTS."""
    text = unicodedata.normalize("NFC", text or "")
    text = _TAG_RE.sub("", text)
    text = text.replace("\ufeff", "")
    text = text.replace("♪", "")
    text = _WS_RE.sub(" ", text).strip()
    return text


def truncate(text: str, max_len: int = 250) -> str:
    text = text or ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
