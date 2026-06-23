"""Redis-backed dubbing worker.

Run with: python run_worker.py
"""

from __future__ import annotations

import asyncio
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
from app.services.supabase_service import supabase_service
from app.services.task_service import update_task_status
from app.services.video_service import merge_audio_with_video
from app.states import STATE_IDLE, TASK_COMPLETED, TASK_FAILED, TASK_PROCESSING
from app.utils.file_utils import check_ffmpeg_available, clean_task_files, delete_file


async def _edit_progress(bot: Bot, chat_id: int, message_id: int | None, text: str) -> None:
    if not message_id:
        return
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
    except Exception:
        pass


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
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=60,
                    pool_timeout=60,
                )
            return
        except TelegramError as exc:
            last_error = exc
            logger.warning("Send video attempt %s failed: %s", attempt, exc)
            await asyncio.sleep(min(attempt * 2, 8))
    raise RuntimeError(f"Failed to send final video: {last_error}")


async def process_task(bot: Bot, payload: Dict[str, Any]) -> None:
    task_id = payload["task_id"]
    chat_id = int(payload["chat_id"])
    telegram_user_id = int(payload["telegram_user_id"])
    progress_message_id = int(payload.get("progress_message_id") or 0) or None
    video_path = Path(payload["video_path"])
    srt_path = Path(payload["srt_path"])
    voice = payload["voice"]
    video_duration = float(payload["video_duration"])
    output_path = settings.output_dir / f"{task_id}_dubbed.mp4"

    async def progress(percent: int, text: str) -> None:
        await redis_service.set_task_status(task_id, TASK_PROCESSING, percent)
        try:
            await supabase_service.update_task(task_id, {"status": TASK_PROCESSING, "progress": percent})
        except Exception:
            pass
        await _edit_progress(bot, chat_id, progress_message_id, text)

    try:
        await update_task_status(task_id, TASK_PROCESSING, 15)
        await progress(15, "កំពុងរៀបចំឯកសារ... 15%")

        subtitles = validate_srt_file(srt_path, video_duration)
        await progress(20, "កំពុងអានអក្សរ SRT... 20%")

        dubbed_audio = await build_dubbed_audio(
            task_id=task_id,
            subtitles=subtitles,
            voice=voice,
            video_duration=video_duration,
            progress_callback=progress,
        )
        await progress(75, "កំពុងរៀបចំសម្លេងចុងក្រោយ... 75%")

        await merge_audio_with_video(video_path, dubbed_audio, output_path)
        await progress(90, "កំពុងបញ្ចូលសម្លេងទៅក្នុងវីដេអូ... 90%")

        await update_task_status(task_id, TASK_COMPLETED, 100, output_file_path=str(output_path))
        await _edit_progress(bot, chat_id, progress_message_id, "រួចរាល់ ✅")
        await _send_video_with_retry(bot, chat_id, output_path)
        await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
        await redis_service.delete(f"user:{telegram_user_id}:task")

        if settings.clean_success_files:
            clean_task_files([video_path, srt_path, output_path])
            # Remove task audio folder recursively.
            audio_dir = settings.audio_dir / task_id
            if audio_dir.exists():
                import shutil

                shutil.rmtree(audio_dir, ignore_errors=True)
        await log_db("info", "worker", "Task completed", {"task_id": task_id, "user": telegram_user_id})
    except Exception as exc:
        err = str(exc)
        await update_task_status(task_id, TASK_FAILED, error_message=err)
        await log_db(
            "error",
            "worker",
            "Task failed",
            {"task_id": task_id, "error": err, "traceback": traceback.format_exc()},
        )
        await _edit_progress(
            bot,
            chat_id,
            progress_message_id,
            "សូមទោស មានបញ្ហាក្នុងការដំណើរការ។ សូមព្យាយាមម្តងទៀត។",
        )
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="សូមទោស មានបញ្ហាក្នុងការដំណើរការ។ សូមព្យាយាមម្តងទៀត។",
            )
        except Exception:
            pass
        if not settings.keep_failed_files:
            clean_task_files([video_path, srt_path, output_path])


async def worker_main() -> None:
    settings.ensure_dirs()
    check_ffmpeg_available()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    redis_ok = await redis_service.ping()
    supabase_ok = await supabase_service.health_check()
    if not redis_ok:
        raise RuntimeError("Redis connection failed")
    if not supabase_ok:
        raise RuntimeError("Supabase connection failed")

    logger.info("Dubbing worker started. Queue=%s", settings.redis_queue_key)
    async with Bot(token=settings.bot_token) as bot:
        while True:
            try:
                payload = await redis_service.dequeue(timeout=5)
                if payload is None:
                    continue
                logger.info("Processing task %s", payload.get("task_id"))
                await process_task(bot, payload)
            except asyncio.CancelledError:
                raise
            except KeyboardInterrupt:
                break
            except Exception as exc:
                logger.exception("Worker loop error: %s", exc)
                await asyncio.sleep(2)

    await redis_service.close()
