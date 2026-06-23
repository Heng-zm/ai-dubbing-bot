"""Video probing and ffmpeg merge service."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.utils.file_utils import run_subprocess


async def get_media_duration(path: Path) -> float:
    cmd = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = await run_subprocess(cmd, timeout=30)
    data = json.loads(result.stdout or "{}")
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError("Could not detect media duration")
    return duration


async def has_audio_stream(path: Path) -> bool:
    cmd = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = await run_subprocess(cmd, timeout=30)
        data = json.loads(result.stdout or "{}")
        return bool(data.get("streams"))
    except Exception:
        return False


async def merge_audio_with_video(
    video_path: Path,
    dubbed_audio_path: Path,
    output_path: Path,
    keep_original_audio: bool | None = None,
) -> Path:
    """Merge dubbed audio with video, optionally mixing original audio at low volume."""
    keep_original = settings.keep_original_audio if keep_original_audio is None else keep_original_audio
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if keep_original and not await has_audio_stream(video_path):
        keep_original = False

    if keep_original:
        filter_complex = (
            f"[0:a]volume={settings.original_audio_volume}[orig];"
            f"[1:a]volume={settings.dubbed_audio_volume}[dub];"
            "[orig][dub]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            settings.ffmpeg_binary,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(dubbed_audio_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
    else:
        cmd = [
            settings.ffmpeg_binary,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(dubbed_audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]

    await run_subprocess(cmd, timeout=600)
    return output_path
