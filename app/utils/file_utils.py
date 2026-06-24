"""File, cleanup, and subprocess helpers."""

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
    for folder in [settings.videos_dir, settings.subtitles_dir, settings.audio_dir, settings.output_dir, settings.tts_cache_dir]:
        if not folder.exists():
            continue
        for item in sorted(folder.rglob("*"), reverse=True):
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink()
                    deleted += 1
                elif item.is_dir() and not any(item.iterdir()):
                    item.rmdir()
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
            "Missing required binary: "
            + ", ".join(missing)
            + ". Install ffmpeg and make sure ffmpeg/ffprobe are in PATH."
        )


def _short_command(cmd: list[str], max_items: int = 10) -> str:
    return " ".join(str(item) for item in cmd[:max_items]) + (" ..." if len(cmd) > max_items else "")


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Stop a subprocess without leaving ffmpeg/ffprobe running after cancellation."""
    if process.returncode is not None:
        return
    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=5)
    except Exception:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except Exception:
            pass


async def run_subprocess(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg/ffprobe safely without blocking the event loop.

    The older implementation used ``subprocess.run`` inside ``asyncio.to_thread``.
    When Render restarted or a task was cancelled, the Python coroutine could stop
    while ffmpeg kept running in the background until its timeout. This async
    implementation terminates the child process on timeout/cancellation and keeps
    useful stderr/stdout snippets for admin logs.
    """
    command = _short_command(cmd)
    try:
        process = await asyncio.create_subprocess_exec(
            *[str(item) for item in cmd],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command binary not found: {cmd[0]}") from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _terminate_process(process)
        raise RuntimeError(f"Command timed out after {timeout}s: {command}") from exc
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise

    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    completed = subprocess.CompletedProcess(cmd, process.returncode or 0, stdout, stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {command}\n"
            f"STDERR: {(stderr or '').strip()[-3000:]}\n"
            f"STDOUT: {(stdout or '').strip()[-1000:]}"
        )
    return completed
