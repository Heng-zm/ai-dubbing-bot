"""Telegram file validation and download helpers."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Tuple

from telegram import Message

from app.config import settings
from app.services.runtime_settings import runtime_settings
from app.services.video_service import get_media_duration
from app.utils.file_utils import safe_suffix

ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}


def get_video_file_info(message: Message) -> Tuple[str, int | None, str, int | None]:
    """Return file_id, file_size, filename, telegram_duration from a video or video document."""
    if message.video:
        video = message.video
        filename = video.file_name or f"telegram_video_{video.file_unique_id}.mp4"
        return video.file_id, video.file_size, filename, video.duration

    if message.document:
        doc = message.document
        filename = doc.file_name or f"telegram_document_{doc.file_unique_id}"
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_VIDEO_SUFFIXES:
            raise ValueError("unsupported_video_format")
        return doc.file_id, doc.file_size, filename, None

    raise ValueError("no_video")


async def get_srt_file_info(message: Message) -> Tuple[str, int | None, str]:
    if not message.document:
        raise ValueError("no_srt")
    doc = message.document
    filename = doc.file_name or "subtitle.srt"
    if Path(filename).suffix.lower() != ".srt":
        raise ValueError("not_srt")
    max_srt_size_mb = await runtime_settings.get_int("max_srt_size_mb")
    if doc.file_size and doc.file_size > max_srt_size_mb * 1024 * 1024:
        raise ValueError("srt_too_large")
    return doc.file_id, doc.file_size, filename


async def download_telegram_file(bot, file_id: str, dest_path: Path) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tg_file = await bot.get_file(file_id)
    await tg_file.download_to_drive(custom_path=str(dest_path))
    return dest_path


async def download_and_validate_video(message: Message, bot) -> dict:
    file_id, file_size, filename, telegram_duration = get_video_file_info(message)
    suffix = safe_suffix(filename, ".mp4").lower()
    if suffix not in ALLOWED_VIDEO_SUFFIXES:
        raise ValueError("unsupported_video_format")

    runtime = await runtime_settings.load()
    max_video_size_bytes = int(runtime["max_video_size_mb"]) * 1024 * 1024
    max_video_duration_seconds = int(runtime["max_video_duration_seconds"])

    if file_size and file_size > max_video_size_bytes:
        raise ValueError("video_too_large")

    if telegram_duration and telegram_duration > max_video_duration_seconds:
        raise ValueError("video_too_long")

    local_name = f"{uuid.uuid4().hex}{suffix}"
    path = settings.videos_dir / local_name
    await download_telegram_file(bot, file_id, path)

    actual_size = path.stat().st_size if path.exists() else 0
    if actual_size <= 0:
        path.unlink(missing_ok=True)
        raise ValueError("no_video")
    if actual_size > max_video_size_bytes:
        path.unlink(missing_ok=True)
        raise ValueError("video_too_large")

    duration = await get_media_duration(path)
    if duration > max_video_duration_seconds + 0.5:
        path.unlink(missing_ok=True)
        raise ValueError("video_too_long")

    return {
        "file_id": file_id,
        "file_size": actual_size,
        "filename": filename,
        "path": str(path),
        "duration": duration,
    }


async def download_srt(message: Message, bot, task_id: str) -> dict:
    file_id, file_size, filename = await get_srt_file_info(message)
    path = settings.subtitles_dir / f"{task_id}.srt"
    await download_telegram_file(bot, file_id, path)
    max_srt_size_mb = await runtime_settings.get_int("max_srt_size_mb")
    if path.stat().st_size > max_srt_size_mb * 1024 * 1024:
        path.unlink(missing_ok=True)
        raise ValueError("srt_too_large")
    return {"file_id": file_id, "file_size": path.stat().st_size, "filename": filename, "path": str(path)}
