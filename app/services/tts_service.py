"""Microsoft Edge TTS generation service."""

from __future__ import annotations

import asyncio
from pathlib import Path

import edge_tts

from app.config import settings
from app.services.logger_service import logger
from app.utils.text_utils import normalize_tts_text


async def generate_tts_audio(text: str, voice: str, output_path: Path) -> Path:
    """Generate one subtitle line with retry logic."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    clean_text = normalize_tts_text(text)
    if not clean_text:
        raise ValueError("Cannot generate TTS from empty subtitle text")

    last_error: Exception | None = None
    for attempt in range(1, settings.tts_max_retries + 1):
        try:
            communicate = edge_tts.Communicate(clean_text, voice)
            await communicate.save(str(output_path))
            if output_path.exists() and output_path.stat().st_size > 0:
                return output_path
            raise RuntimeError("edge-tts produced an empty file")
        except Exception as exc:  # pragma: no cover - network/service dependent
            last_error = exc
            logger.warning("TTS attempt %s/%s failed: %s", attempt, settings.tts_max_retries, exc)
            await asyncio.sleep(min(2 * attempt, 6))
    raise RuntimeError(f"TTS generation failed after retries: {last_error}")
