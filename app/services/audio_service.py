"""Audio segment timing, padding, speed adjustment, and final dubbed track creation."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, List

from app.config import settings
from app.services.srt_parser import SubtitleItem
from app.services.tts_service import generate_tts_audio
from app.services.video_service import get_media_duration
from app.utils.file_utils import run_subprocess


def _atempo_chain(speed: float) -> str:
    """Build ffmpeg atempo chain. Each atempo filter should be between 0.5 and 2.0."""
    speed = max(speed, 0.25)
    parts: List[float] = []
    remaining = speed
    while remaining > 2.0:
        parts.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        parts.append(0.5)
        remaining /= 0.5
    parts.append(remaining)
    return ",".join(f"atempo={p:.5f}" for p in parts)


async def create_silence(path: Path, duration: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, duration)
    cmd = [
        settings.ffmpeg_binary,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=stereo",
        "-t",
        f"{duration:.3f}",
        "-acodec",
        "pcm_s16le",
        str(path),
    ]
    await run_subprocess(cmd, timeout=60)
    return path


async def fit_audio_to_duration(input_path: Path, output_path: Path, target_duration: float) -> Path:
    """Pad, gently speed up, or trim TTS audio to exactly fit a subtitle window."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_duration = await get_media_duration(input_path)
    target_duration = max(0.05, target_duration)

    # If audio is meaningfully longer, speed it up to fit. If extremely long, speed plus trim.
    filters: List[str] = []
    if source_duration > target_duration * 1.03:
        speed = source_duration / target_duration
        filters.append(_atempo_chain(speed))

    # apad pads short audio; atrim enforces exact subtitle duration.
    filters.extend(["apad", f"atrim=0:{target_duration:.3f}", "asetpts=N/SR/TB"])
    filter_str = ",".join(filters)

    cmd = [
        settings.ffmpeg_binary,
        "-y",
        "-i",
        str(input_path),
        "-af",
        filter_str,
        "-t",
        f"{target_duration:.3f}",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-acodec",
        "pcm_s16le",
        str(output_path),
    ]
    await run_subprocess(cmd, timeout=120)
    return output_path


async def concat_wav_files(files: Iterable[Path], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = output_path.with_suffix(".concat.txt")
    lines = []
    for file in files:
        # ffmpeg concat file format expects single quotes escaped.
        safe_path = str(file.resolve()).replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    list_file.write_text("\n".join(lines), encoding="utf-8")
    cmd = [
        settings.ffmpeg_binary,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output_path),
    ]
    await run_subprocess(cmd, timeout=300)
    try:
        list_file.unlink(missing_ok=True)
    except Exception:
        pass
    return output_path


async def normalize_audio(input_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        settings.ffmpeg_binary,
        "-y",
        "-i",
        str(input_path),
        "-af",
        f"volume={settings.dubbed_audio_volume},loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(output_path),
    ]
    await run_subprocess(cmd, timeout=300)
    return output_path


async def build_dubbed_audio(
    task_id: str,
    subtitles: List[SubtitleItem],
    voice: str,
    video_duration: float,
    progress_callback=None,
) -> Path:
    """Generate all TTS clips and build a single timed dubbed WAV track."""
    task_audio_dir = settings.audio_dir / task_id
    task_audio_dir.mkdir(parents=True, exist_ok=True)
    timeline_files: List[Path] = []
    cursor = 0.0
    total = max(len(subtitles), 1)

    for idx, item in enumerate(subtitles, start=1):
        if item.start > cursor + 0.02:
            gap_path = task_audio_dir / f"gap_{idx:04d}.wav"
            await create_silence(gap_path, item.start - cursor)
            timeline_files.append(gap_path)

        raw_path = task_audio_dir / f"tts_raw_{idx:04d}.mp3"
        fitted_path = task_audio_dir / f"tts_fit_{idx:04d}.wav"
        await generate_tts_audio(item.text, voice, raw_path)
        await fit_audio_to_duration(raw_path, fitted_path, item.duration)
        timeline_files.append(fitted_path)
        cursor = max(cursor, item.end)

        if progress_callback:
            # Map subtitle generation from roughly 15% to 70%.
            percent = 15 + math.floor((idx / total) * 55)
            await progress_callback(percent, f"កំពុងបង្កើតសម្លេង AI... {percent}%")

    if video_duration > cursor + 0.02:
        tail_path = task_audio_dir / "tail_silence.wav"
        await create_silence(tail_path, video_duration - cursor)
        timeline_files.append(tail_path)

    if not timeline_files:
        silence = task_audio_dir / "empty.wav"
        await create_silence(silence, video_duration)
        timeline_files.append(silence)

    concat_path = task_audio_dir / "dubbed_concat.wav"
    normalized_path = task_audio_dir / "dubbed_final.wav"
    await concat_wav_files(timeline_files, concat_path)
    await normalize_audio(concat_path, normalized_path)
    return normalized_path
