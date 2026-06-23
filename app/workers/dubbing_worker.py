"""Background worker for AI dubbing tasks."""

from __future__ import annotations

import asyncio
import shutil
import time
import traceback
from pathlib import Path
from typing import Any, Dict

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from app.config import settings
from app.services.audio_service import build_dubbed_audio
from app.services.error_recovery import classify_error, recovery_keyboard
from app.services.logger_service import log_db, logger
from app.services.redis_service import redis_service
from app.services.runtime_settings import runtime_settings
from app.services.srt_parser import validate_srt_file
from app.services.task_service import update_task_status
from app.services.video_service import merge_audio_with_video
from app.states import STATE_IDLE, TASK_CANCELLED, TASK_COMPLETED, TASK_FAILED, TASK_PROCESSING
from app.utils.file_utils import check_ffmpeg_available, clean_task_files
from app.utils.telegram_ui import percent_line



class StaleTaskFileError(RuntimeError):
    """Raised when a queued job points to a local temp file that no longer exists."""


class UserCancelledTask(RuntimeError):
    """Raised when a task was cancelled while a worker was between stages."""


async def _raise_if_cancelled(task_id: str) -> None:
    # Keep the worker lock alive during long TTS/audio stages and stop quickly
    # when a user/admin cancels. Refreshing a missing key is harmless.
    await redis_service.refresh_task_lock(task_id)
    status = await redis_service.get_task_status(task_id)
    if status.get("status") == TASK_CANCELLED:
        raise UserCancelledTask("Task cancelled by user/admin")


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
        current = await redis_service.get_task_status(self.task_id)
        current_status = current.get("status")
        if current_status == TASK_CANCELLED:
            raise UserCancelledTask("Task cancelled by user/admin")
        # Do not overwrite terminal statuses. The old code changed Redis from
        # completed/failed/cancelled back to processing when editing the final
        # progress message.
        if current_status not in {TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED}:
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
                    caption="✅ ការបញ្ចូលសម្លេងរឿងរួចរាល់ហើយ!",
                    supports_streaming=True,
                    read_timeout=180,
                    write_timeout=180,
                    connect_timeout=60,
                    pool_timeout=60,
                )
            break
        except TelegramError as exc:
            last_error = exc
            logger.warning("Send video attempt %s/%s failed: %s", attempt, settings.telegram_send_max_retries, exc)
            await asyncio.sleep(min(attempt * 3, 12))
    else:
        raise RuntimeError(f"Failed to send final video: {last_error}")

    # The follow-up message is helpful but should never cause duplicate video sends.
    try:
        await bot.send_message(
            chat_id=chat_id,
            text='ចុច Button "Start" ខាងក្រោមដើម្បីបន្ត។',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Start", callback_data="start_dubbing")]]),
        )
    except TelegramError as exc:
        logger.warning("Could not send completion Start button: %s", exc)


def _require_payload_path(payload: Dict[str, Any], name: str) -> Path:
    value = payload.get(name)
    if not value:
        raise StaleTaskFileError(f"Missing task payload path: {name}")
    path = Path(str(value))
    if not path.exists():
        raise StaleTaskFileError(
            f"Task file missing: {name}={path}. "
            "This usually happens after a Render redeploy/restart because temp files are local and not durable."
        )
    return path


async def process_task(bot: Bot, payload: Dict[str, Any], worker_name: str = "dubbing-worker") -> None:
    task_id = str(payload["task_id"])
    owner = f"{worker_name}:{id(asyncio.current_task())}"
    if not await redis_service.acquire_task_lock(task_id, owner):
        current_status = await redis_service.get_task_status(task_id)
        if current_status.get("status") in {TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED}:
            logger.info("Dropping duplicate locked job for terminal task %s", task_id)
            return
        requeues = int(payload.get("_lock_requeues") or 0)
        if requeues >= 5:
            logger.warning("Task %s stayed locked after %s requeues; dropping duplicate queue job.", task_id, requeues)
            return
        logger.warning("Task %s is already locked by another worker. Requeueing after delay (%s/5).", task_id, requeues + 1)
        new_payload = dict(payload)
        new_payload["_lock_requeues"] = requeues + 1
        asyncio.create_task(redis_service.requeue_later(new_payload, delay_seconds=5))
        return

    chat_id = int(payload["chat_id"])
    telegram_user_id = int(payload["telegram_user_id"])
    progress_message_id = int(payload.get("progress_message_id") or 0) or None
    video_path = Path("")
    srt_path = Path("")
    output_path = settings.output_dir / f"{task_id}_dubbed.mp4"

    try:
        current_status = await redis_service.get_task_status(task_id)
        if current_status.get("status") in {TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED}:
            logger.info("Skipping terminal task %s with status=%s", task_id, current_status.get("status"))
            return

        video_path = _require_payload_path(payload, "video_path")
        srt_path = _require_payload_path(payload, "srt_path")
        voice = str(payload.get("voice") or "")
        if not voice:
            raise RuntimeError("Missing selected TTS voice")
        video_duration = float(payload.get("video_duration") or 0)

        progress = ProgressReporter(bot, chat_id, progress_message_id, task_id)
        await update_task_status(task_id, TASK_PROCESSING, 15, mark_started=True)
        await progress.edit(15, f"⚙️ កំពុងរៀបចំឯកសារ...\n\n{percent_line(15)}", force=True)

        await _raise_if_cancelled(task_id)
        subtitles = validate_srt_file(srt_path, video_duration)
        await progress.edit(20, f"📝 កំពុងអាន Subtitle SRT...\n\n{percent_line(20)}", force=True)

        await _raise_if_cancelled(task_id)
        await redis_service.refresh_task_lock(task_id)
        dubbed_audio = await build_dubbed_audio(
            task_id=task_id,
            subtitles=subtitles,
            voice=voice,
            video_duration=video_duration,
            progress_callback=progress.edit,
            cancel_check=lambda: _raise_if_cancelled(task_id),
        )
        await progress.edit(78, f"🔊 កំពុងរៀបចំសម្លេងចុងក្រោយ...\n\n{percent_line(78)}", force=True)

        await _raise_if_cancelled(task_id)
        await redis_service.refresh_task_lock(task_id)
        await merge_audio_with_video(video_path, dubbed_audio, output_path)
        await progress.edit(92, f"🎬 កំពុងបញ្ចូលសម្លេងទៅក្នុងវីដេអូ...\n\n{percent_line(92)}", force=True)

        await _send_video_with_retry(bot, chat_id, output_path)
        await update_task_status(task_id, TASK_COMPLETED, 100, output_file_path=str(output_path), mark_finished=True)
        await progress.edit(100, f"✅ រួចរាល់!\n\n{percent_line(100)}", force=True)
        await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
        await redis_service.delete(f"user:{telegram_user_id}:task")

        runtime = await runtime_settings.load()
        if bool(runtime.get("clean_success_files", settings.clean_success_files)):
            clean_task_files([video_path, srt_path, output_path])
            audio_dir = settings.audio_dir / task_id
            if audio_dir.exists():
                shutil.rmtree(audio_dir, ignore_errors=True)
        await log_db("info", "worker", "Task completed", {"task_id": task_id, "user": telegram_user_id})
    except UserCancelledTask as exc:
        await update_task_status(task_id, TASK_CANCELLED, progress=0, error_message=str(exc), mark_finished=True)
        await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
        await redis_service.delete(f"user:{telegram_user_id}:task")
        await log_db("info", "worker", "Task cancelled during processing", {"task_id": task_id, "user": telegram_user_id})
    except Exception as exc:
        err = str(exc)
        recovery = classify_error(exc)
        is_stale_file = recovery.category == "stale_file" or isinstance(exc, StaleTaskFileError)
        user_message = f"❌ {recovery.title}\n\n{recovery.user_message}"
        await update_task_status(task_id, TASK_FAILED, error_message=f"[{recovery.category}] {err}", mark_finished=True)
        await log_db(
            "warning" if is_stale_file else "error",
            "worker",
            "Task failed with smart recovery",
            {
                "task_id": task_id,
                "category": recovery.category,
                "admin_hint": recovery.admin_hint,
                "error": err,
                "traceback": traceback.format_exc(),
            },
        )
        runtime = await runtime_settings.load()
        keep_failed_files = bool(runtime.get("keep_failed_files", settings.keep_failed_files))
        retry_markup = recovery_keyboard(task_id, keep_failed_files and recovery.retry_allowed and not is_stale_file)
        try:
            if progress_message_id:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_message_id,
                    text=user_message,
                    reply_markup=retry_markup,
                )
            else:
                await bot.send_message(chat_id=chat_id, text=user_message, reply_markup=retry_markup)
        except Exception:
            pass
        await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
        await redis_service.delete(f"user:{telegram_user_id}:task")
        if not keep_failed_files:
            clean_task_files([video_path, srt_path, output_path])
            audio_dir = settings.audio_dir / task_id
            if audio_dir.exists():
                shutil.rmtree(audio_dir, ignore_errors=True)
    finally:
        await redis_service.release_task_lock(task_id, owner)


async def worker_loop(bot: Bot, name: str = "dubbing-worker") -> None:
    """Continuously consume Redis queue jobs."""
    logger.info("%s started. Queue=%s", name, await runtime_settings.get_str("redis_queue_key"))
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
