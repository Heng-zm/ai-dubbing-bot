"""Video probing, watermarking, and ffmpeg merge service.

The Render single-service deployment has limited CPU/RAM. Visible drawtext
watermarking forces a full video re-encode and was the common reason tasks looked
stuck around 91% or the service restarted during ffmpeg. The default watermark
mode is now metadata branding, which keeps branding in the MP4 metadata while
allowing fast video stream copy whenever the source codec is MP4-compatible.
Admins can still enable a visible watermark from /admin -> Settings by setting
watermark_render_mode=visible.
"""

from __future__ import annotations

import json
import os
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
    # Conservative escaping for ffmpeg drawtext values.
    return (
        str(text or "Dubbed by @aidubbingkhbot")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "’")
        .replace("%", "\\%")
        .replace("\n", " ")
    )


def _metadata_value(text: str) -> str:
    return str(text or "Dubbed by @aidubbingkhbot").replace("\n", " ").strip()[:240]


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
        "-threads",
        "1",
    ]


def _metadata_args(enabled: bool, text: str) -> list[str]:
    if not enabled:
        return []
    value = _metadata_value(text)
    return [
        "-metadata",
        f"title={value}",
        "-metadata",
        f"comment={value}",
        "-metadata",
        f"description={value}",
    ]


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


async def merge_audio_with_video(
    video_path: Path,
    dubbed_audio_path: Path,
    output_path: Path,
    keep_original_audio: bool | None = None,
) -> Path:
    """Merge dubbed audio with video and optionally brand the output.

    Default behaviour is optimized for Render:
    - keep/copy the original video stream when possible;
    - encode only audio;
    - store watermark branding as MP4 metadata.

    Visible watermark is still supported but it is slower because ffmpeg must
    re-encode the video. If visible watermarking fails or times out, the code
    automatically retries with metadata branding instead of leaving the user at
    91% forever.
    """
    runtime = await runtime_settings.load()
    keep_original_default = bool(runtime.get("keep_original_audio", settings.keep_original_audio))
    original_audio_volume = float(runtime.get("original_audio_volume", settings.original_audio_volume))
    dubbed_audio_volume = float(runtime.get("dubbed_audio_volume", settings.dubbed_audio_volume))
    watermark_enabled = bool(runtime.get("watermark_enabled", True))
    watermark_mode = str(runtime.get("watermark_render_mode", "metadata") or "metadata").lower()
    watermark_text = str(runtime.get("watermark_text", "Dubbed by @aidubbingkhbot") or "Dubbed by @aidubbingkhbot")
    watermark_position = str(runtime.get("watermark_position", "bottom_right") or "bottom_right")

    if watermark_mode not in {"metadata", "visible", "off"}:
        watermark_mode = "metadata"
    if not watermark_enabled:
        watermark_mode = "off"

    keep_original = keep_original_default if keep_original_audio is None else keep_original_audio
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if keep_original and not await has_audio_stream(video_path):
        keep_original = False

    tmp_output = output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")
    _safe_unlink(tmp_output)
    _safe_unlink(output_path)

    async def _run_merge(mode: str) -> None:
        visible_watermark = mode == "visible"
        metadata_branding = mode == "metadata"
        copy_video = (await _can_copy_video_to_mp4(video_path)) and not visible_watermark
        video_args = _video_encode_args(copy_video)
        metadata_args = _metadata_args(metadata_branding, watermark_text)
        drawtext = _watermark_filter(watermark_text, watermark_position) if visible_watermark else ""

        common = [
            settings.ffmpeg_binary,
            "-y",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(video_path),
            "-i",
            str(dubbed_audio_path),
            "-max_muxing_queue_size",
            "1024",
        ]

        if visible_watermark and keep_original:
            filter_complex = (
                f"[0:v]{drawtext}[vout];"
                f"[0:a]volume={original_audio_volume}[orig];"
                f"[1:a]volume={dubbed_audio_volume}[dub];"
                "[orig][dub]amix=inputs=2:duration=first:dropout_transition=0[aout]"
            )
            cmd = common + [
                "-filter_complex",
                filter_complex,
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                *video_args,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                *metadata_args,
                "-movflags",
                "+faststart",
                str(tmp_output),
            ]
        elif visible_watermark:
            filter_complex = f"[0:v]{drawtext}[vout];[1:a]volume={dubbed_audio_volume}[aout]"
            cmd = common + [
                "-filter_complex",
                filter_complex,
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                *video_args,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                *metadata_args,
                "-movflags",
                "+faststart",
                str(tmp_output),
            ]
        elif keep_original:
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
                *video_args,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                *metadata_args,
                "-movflags",
                "+faststart",
                str(tmp_output),
            ]
        else:
            cmd = common + [
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                *video_args,
                "-af",
                f"volume={dubbed_audio_volume}",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                *metadata_args,
                "-movflags",
                "+faststart",
                str(tmp_output),
            ]

        plan = "copy-video" if copy_video else "encode-video"
        logger.info(
            "worker | ffmpeg merge plan | mode=%s watermark_enabled=%s keep_original=%s video_plan=%s output=%s",
            mode,
            watermark_enabled,
            keep_original,
            plan,
            tmp_output,
        )
        await run_subprocess(cmd, timeout=settings.ffmpeg_merge_timeout_seconds)

    try:
        await _run_merge(watermark_mode)
    except Exception as exc:
        _safe_unlink(tmp_output)
        if watermark_mode == "visible":
            logger.warning("Visible watermark merge failed; retrying fast metadata merge: %s", exc)
            await _run_merge("metadata")
        else:
            raise

    if not tmp_output.exists() or tmp_output.stat().st_size <= 0:
        raise RuntimeError("ffmpeg output file was not created or is empty")
    os.replace(tmp_output, output_path)
    return output_path
