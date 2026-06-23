"""Video probing, watermarking, and ffmpeg merge service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.config import settings
from app.services.logger_service import logger
from app.services.runtime_settings import runtime_settings
from app.utils.file_utils import run_subprocess

MP4_COPY_VIDEO_CODECS = {"h264", "hevc", "mpeg4"}


async def probe_media(path: Path) -> Dict[str, Any]:
    cmd = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height",
        "-of",
        "json",
        str(path),
    ]
    result = await run_subprocess(cmd, timeout=30)
    return json.loads(result.stdout or "{}")


async def get_media_duration(path: Path) -> float:
    data = await probe_media(path)
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError("Could not detect media duration")
    return duration


async def has_audio_stream(path: Path) -> bool:
    data = await probe_media(path)
    return any(stream.get("codec_type") == "audio" for stream in data.get("streams", []))


async def get_video_codec(path: Path) -> str:
    data = await probe_media(path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            return str(stream.get("codec_name") or "").lower()
    return ""


async def _can_copy_video_to_mp4(path: Path) -> bool:
    codec = await get_video_codec(path)
    return codec in MP4_COPY_VIDEO_CODECS


def _drawtext_escape(text: str) -> str:
    # Keep this conservative. It avoids ffmpeg drawtext option separator issues.
    return (
        str(text or "Dubbed by @aidubbingkhbot")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "’")
        .replace("%", "\\%")
        .replace("\n", " ")
    )


def _watermark_filter(text: str, position: str) -> str:
    escaped_text = _drawtext_escape(text)
    positions = {
        "bottom_right": "x=w-tw-24:y=h-th-24",
        "bottom_left": "x=24:y=h-th-24",
        "top_right": "x=w-tw-24:y=24",
        "top_left": "x=24:y=24",
    }
    xy = positions.get(str(position or "bottom_right"), positions["bottom_right"])
    return (
        f"drawtext=text='{escaped_text}':{xy}:fontsize=24:"
        "fontcolor=white@0.92:box=1:boxcolor=black@0.38:boxborderw=10"
    )


def _video_encode_args(copy_video: bool) -> list[str]:
    if copy_video:
        return ["-c:v", "copy"]
    return [
        "-c:v",
        "libx264",
        "-preset",
        settings.ffmpeg_preset,
        "-crf",
        str(settings.ffmpeg_video_crf),
        "-pix_fmt",
        "yuv420p",
    ]


async def merge_audio_with_video(
    video_path: Path,
    dubbed_audio_path: Path,
    output_path: Path,
    keep_original_audio: bool | None = None,
) -> Path:
    """Merge dubbed audio with video and optionally add branding watermark.

    When watermark is enabled the video must be re-encoded because drawtext is a
    video filter. If drawtext/fontconfig is not available on the host, the code
    retries once without watermark so the user still gets a final video.
    """
    runtime = await runtime_settings.load()
    keep_original_default = bool(runtime.get("keep_original_audio", settings.keep_original_audio))
    original_audio_volume = float(runtime.get("original_audio_volume", settings.original_audio_volume))
    dubbed_audio_volume = float(runtime.get("dubbed_audio_volume", settings.dubbed_audio_volume))
    watermark_enabled = bool(runtime.get("watermark_enabled", True))
    watermark_text = str(runtime.get("watermark_text", "Dubbed by @aidubbingkhbot") or "Dubbed by @aidubbingkhbot")
    watermark_position = str(runtime.get("watermark_position", "bottom_right") or "bottom_right")

    keep_original = keep_original_default if keep_original_audio is None else keep_original_audio
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if keep_original and not await has_audio_stream(video_path):
        keep_original = False

    async def _run_merge(use_watermark: bool) -> None:
        copy_video = (await _can_copy_video_to_mp4(video_path)) and not use_watermark
        video_args = _video_encode_args(copy_video)
        video_filter_args = ["-vf", _watermark_filter(watermark_text, watermark_position)] if use_watermark else []

        common = [
            settings.ffmpeg_binary,
            "-y",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(video_path),
            "-i",
            str(dubbed_audio_path),
        ]

        if keep_original:
            filter_complex = (
                f"[0:a]volume={original_audio_volume}[orig];"
                f"[1:a]volume={dubbed_audio_volume}[dub];"
                "[orig][dub]amix=inputs=2:duration=first:dropout_transition=0[aout]"
            )
            cmd = common + [
                "-filter_complex",
                filter_complex,
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                *video_filter_args,
                *video_args,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(output_path),
            ]
        else:
            cmd = common + [
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                *video_filter_args,
                *video_args,
                "-af",
                f"volume={dubbed_audio_volume}",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(output_path),
            ]
        await run_subprocess(cmd, timeout=900)

    try:
        await _run_merge(watermark_enabled)
    except Exception as exc:
        if not watermark_enabled:
            raise
        logger.warning("Watermark merge failed; retrying without watermark: %s", exc)
        output_path.unlink(missing_ok=True)
        await _run_merge(False)
    return output_path
