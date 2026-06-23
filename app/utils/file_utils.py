"""File and subprocess helpers."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List

from app.config import settings


def safe_suffix(filename: str, default: str = ".bin") -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix else default


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def delete_file(path: str | Path | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def clean_task_files(paths: Iterable[str | Path | None]) -> None:
    for path in paths:
        delete_file(path)


def clean_temp_older_than(hours: int = 24) -> int:
    import time

    cutoff = time.time() - hours * 3600
    deleted = 0
    for folder in [settings.videos_dir, settings.subtitles_dir, settings.audio_dir, settings.output_dir]:
        if not folder.exists():
            continue
        for item in folder.rglob("*"):
            if item.is_file():
                try:
                    if item.stat().st_mtime < cutoff:
                        item.unlink()
                        deleted += 1
                except Exception:
                    continue
    return deleted


def check_binary(binary: str) -> bool:
    return shutil.which(binary) is not None


def check_ffmpeg_available() -> None:
    missing: List[str] = []
    if not check_binary(settings.ffmpeg_binary):
        missing.append(settings.ffmpeg_binary)
    if not check_binary(settings.ffprobe_binary):
        missing.append(settings.ffprobe_binary)
    if missing:
        raise RuntimeError(
            "Missing required binary: " + ", ".join(missing) + ". Install ffmpeg and make sure ffmpeg/ffprobe are in PATH."
        )


async def run_subprocess(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a blocking subprocess off the event loop."""

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )

    return await asyncio.to_thread(_run)
