"""Background worker for AI dubbing tasks."""

from __future__ import annotations

import asyncio
import shutil
import time
import traceback
from pathlib import Path
from typing import Any, Dict

from telegram import Bot
from telegram.error import TelegramError

from app.config import settings
from app.services.audio_service import build_dubbed_audio
from app.services.logger_service import log_db, logger
from app.services.redis_service import redis_service
from app.services.srt_parser import validate_srt_file
from app.services.task_service import update_task_status
from app.services.video_service import merge_audio_with_video
from app.states import STATE_IDLE, TASK_COMPLETED, TASK_FAILED, TASK_PROCESSING
from app.utils.file_utils import check_ffmpeg_available, clean_task_files


class ProgressReporter:
    """Throttle Telegram progress edits to avoid rate limits and noisy updates."""

    def __init__(self, bot: Bot, chat_id: int, message_id: int | None, task_id: str) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.task_id = task_id
        self.last_percent = -1
        self.last_edit_time = 0.0
        self.last_text = ""

    async def edit(self, percent: int, text: str, force: bool = False) -> None:
        percent = max(0, min(100, int(percent)))
        await redis_service.set_task_status(self.task_id, TASK_PROCESSING, percent)
        now = time.monotonic()
        should_edit = (
            force
            or self.last_percent < 0
            or percent >= 100
            or abs(percent - self.last_percent) >= settings.progress_min_delta_percent
            or now - self.last_edit_time >= settings.progress_edit_interval_seconds
        )
        if not should_edit or not self.message_id or text == self.last_text:
            return
        try:
            await self.bot.edit_message_text(chat_id=self.chat_id, message_id=self.message_id, text=text)
            self.last_percent = percent
            self.last_edit_time = now
            self.last_text = text
        except TelegramError as exc:
            # Message may have been deleted or Telegram may reject same text. Keep worker running.
            logger.debug("Could not edit progress message: %s", exc)


async def _send_video_with_retry(bot: Bot, chat_id: int, video_path: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(1, settings.telegram_send_max_retries + 1):
        try:
            with video_path.open("rb") as video_file:
                await bot.send_video(
                    chat_id=chat_id,
                    video=video_file,
                    caption="ការបញ្ចូលសម្លេងរឿងរួចរាល់ហើយ ✅",
                    supports_streaming=True,
                    read_timeout=180,
                    write_timeout=180,
                    connect_timeout=60,
                    pool_timeout=60,
                )
            return
        except TelegramError as exc:
            last_error = exc
            logger.warning("Send video attempt %s/%s failed: %s", attempt, settings.telegram_send_max_retries, exc)
            await asyncio.sleep(min(attempt * 3, 12))
    raise RuntimeError(f"Failed to send final video: {last_error}")


def _require_payload_path(payload: Dict[str, Any], name: str) -> Path:
    value = payload.get(name)
    if not value:
        raise RuntimeError(f"Missing task payload path: {name}")
    path = Path(str(value))
    if not path.exists():
        raise RuntimeError(f"Task file missing: {name}={path}")
    return path


async def process_task(bot: Bot, payload: Dict[str, Any], worker_name: str = "dubbing-worker") -> None:
    task_id = str(payload["task_id"])
    owner = f"{worker_name}:{id(asyncio.current_task())}"
    if not await redis_service.acquire_task_lock(task_id, owner):
        logger.warning("Task %s is already locked by another worker. Requeueing once.", task_id)
        asyncio.create_task(redis_service.requeue_later(payload, delay_seconds=5))
        return

    chat_id = int(payload["chat_id"])
    telegram_user_id = int(payload["telegram_user_id"])
    progress_message_id = int(payload.get("progress_message_id") or 0) or None
    video_path = Path("")
    srt_path = Path("")
    output_path = settings.output_dir / f"{task_id}_dubbed.mp4"

    try:
        current_status = await redis_service.get_task_status(task_id)
        if current_status.get("status") == "cancelled":
            logger.info("Skipping cancelled task %s", task_id)
            return

        video_path = _require_payload_path(payload, "video_path")
        srt_path = _require_payload_path(payload, "srt_path")
        voice = str(payload.get("voice") or "")
        if not voice:
            raise RuntimeError("Missing selected TTS voice")
        video_duration = float(payload.get("video_duration") or 0)

        progress = ProgressReporter(bot, chat_id, progress_message_id, task_id)
        await update_task_status(task_id, TASK_PROCESSING, 15, mark_started=True)
        await progress.edit(15, "កំពុងរៀបចំឯកសារ... 15%", force=True)

        subtitles = validate_srt_file(srt_path, video_duration)
        await progress.edit(20, "កំពុងអានអក្សរ SRT... 20%", force=True)

        await redis_service.refresh_task_lock(task_id)
        dubbed_audio = await build_dubbed_audio(
            task_id=task_id,
            subtitles=subtitles,
            voice=voice,
            video_duration=video_duration,
            progress_callback=progress.edit,
        )
        await progress.edit(78, "កំពុងរៀបចំសម្លេងចុងក្រោយ... 78%", force=True)

        await redis_service.refresh_task_lock(task_id)
        await merge_audio_with_video(video_path, dubbed_audio, output_path)
        await progress.edit(92, "កំពុងបញ្ចូលសម្លេងទៅក្នុងវីដេអូ... 92%", force=True)

        await _send_video_with_retry(bot, chat_id, output_path)
        await update_task_status(task_id, TASK_COMPLETED, 100, output_file_path=str(output_path), mark_finished=True)
        await progress.edit(100, "រួចរាល់ ✅", force=True)
        await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
        await redis_service.delete(f"user:{telegram_user_id}:task")

        if settings.clean_success_files:
            clean_task_files([video_path, srt_path, output_path])
            audio_dir = settings.audio_dir / task_id
            if audio_dir.exists():
                shutil.rmtree(audio_dir, ignore_errors=True)
        await log_db("info", "worker", "Task completed", {"task_id": task_id, "user": telegram_user_id})
    except Exception as exc:
        err = str(exc)
        await update_task_status(task_id, TASK_FAILED, error_message=err, mark_finished=True)
        await log_db(
            "error",
            "worker",
            "Task failed",
            {"task_id": task_id, "error": err, "traceback": traceback.format_exc()},
        )
        try:
            if progress_message_id:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_message_id,
                    text="សូមទោស មានបញ្ហាក្នុងការដំណើរការ។ សូមព្យាយាមម្តងទៀត។",
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text="សូមទោស មានបញ្ហាក្នុងការដំណើរការ។ សូមព្យាយាមម្តងទៀត។",
                )
        except Exception:
            pass
        await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
        await redis_service.delete(f"user:{telegram_user_id}:task")
        if not settings.keep_failed_files:
            clean_task_files([video_path, srt_path, output_path])
            audio_dir = settings.audio_dir / task_id
            if audio_dir.exists():
                shutil.rmtree(audio_dir, ignore_errors=True)
    finally:
        await redis_service.release_task_lock(task_id, owner)


async def worker_loop(bot: Bot, name: str = "dubbing-worker") -> None:
    """Continuously consume Redis queue jobs."""
    logger.info("%s started. Queue=%s", name, settings.redis_queue_key)
    while True:
        try:
            payload = await redis_service.dequeue(timeout=settings.worker_queue_timeout_seconds)
            if payload is None:
                continue
            logger.info("%s processing task %s", name, payload.get("task_id"))
            await process_task(bot, payload, worker_name=name)
        except asyncio.CancelledError:
            logger.info("%s stopped", name)
            raise
        except Exception as exc:
            logger.exception("%s loop error: %s", name, exc)
            await asyncio.sleep(2)


async def worker_main() -> None:
    settings.ensure_dirs()
    check_ffmpeg_available()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    redis_ok = await redis_service.ping()
    from app.services.supabase_service import supabase_service

    supabase_ok = await supabase_service.health_check()
    if not redis_ok:
        raise RuntimeError("Redis connection failed")
    if not supabase_ok:
        raise RuntimeError("Supabase connection failed")

    async with Bot(token=settings.bot_token) as bot:
        try:
            await worker_loop(bot, name="standalone-dubbing-worker")
        finally:
            await redis_service.close()
