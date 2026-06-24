"""Telegram Application factory."""

from __future__ import annotations

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app.config import settings
from app.handlers.admin import admin_callback, admin_command, handle_admin_text
from app.handlers.dubbing import cancel_command, dubbing_callback, handle_video_or_document, status_command
from app.handlers.errors import error_handler
from app.handlers.start import help_callback, help_command, home_callback, start_command, start_dubbing_callback, voice_callback
from app.services.health_server import start_health_server, stop_health_server
from app.services.logger_service import logger
from app.services.redis_service import redis_service
from app.services.runtime_settings import runtime_settings
from app.services.supabase_service import supabase_service
from app.utils.file_utils import check_ffmpeg_available, clean_temp_older_than
from app.states import STATE_IDLE, TASK_COMPLETED, TASK_FAILED, TASK_PROCESSING


async def _recover_interrupted_processing_tasks(application: Application) -> None:
    """Mark tasks interrupted by a Render restart as failed with a retry button.

    When the process restarts while ffmpeg is merging or Telegram is uploading,
    there is no pending Redis queue item left to consume. Without recovery the
    user's progress message can stay at 91% forever. On startup we scan Redis
    task status hashes, fail orphaned processing tasks, release stale locks, and
    notify the user with a clean Khmer retry message when files still exist.
    """
    from pathlib import Path

    from app.services.task_service import update_task_status

    rows = await redis_service.scan_task_statuses(TASK_PROCESSING)
    recovered = 0
    for row in rows:
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        meta = await redis_service.get_task_meta(task_id)
        await redis_service.clear_task_lock(task_id)
        progress = int(float((row.get("status") or {}).get("progress") or 0))
        telegram_user_id = int(meta.get("telegram_user_id") or 0)
        chat_id = int(meta.get("chat_id") or 0)
        message_id = int(meta.get("progress_message_id") or 0)

        # If the final video was already sent but the process restarted before
        # updating the terminal status, mark it completed instead of showing a
        # confusing failed/retry message or sending the video twice.
        if meta.get("final_sent") == "1":
            await update_task_status(
                task_id,
                TASK_COMPLETED,
                progress=100,
                output_file_path=meta.get("output_path") or None,
                mark_finished=True,
            )
            if telegram_user_id:
                await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
                await redis_service.delete(f"user:{telegram_user_id}:task")
            if chat_id and message_id:
                try:
                    await application.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text='✅ Task បានបញ្ចប់រួចហើយ។\n\nចុច Button "Start" ខាងក្រោមដើម្បីបន្ត។',
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Start", callback_data="start_dubbing")]]),
                    )
                except TelegramError as exc:
                    logger.warning("Could not update recovered completed task %s: %s", task_id, exc)
            recovered += 1
            continue

        await update_task_status(
            task_id,
            TASK_FAILED,
            progress=progress,
            error_message="[server_restart] Render restarted while task was processing",
            mark_finished=True,
        )
        video_exists = bool(meta.get("video_path") and Path(str(meta.get("video_path"))).exists())
        srt_exists = bool(meta.get("srt_path") and Path(str(meta.get("srt_path"))).exists())
        retry_allowed = video_exists and srt_exists
        if telegram_user_id:
            await redis_service.set_user_state(telegram_user_id, STATE_IDLE)
            await redis_service.delete(f"user:{telegram_user_id}:task")

        text = (
            "⚠️ Task បានឈប់ពាក់កណ្តាលផ្លូវ ព្រោះ Server បាន Restart។\n\n"
            "សូមទោសចំពោះការរង់ចាំ។ "
            + (
                "អ្នកអាចចុច Retry ដើម្បីដំណើរការម្តងទៀតបាន។"
                if retry_allowed
                else "ឯកសារ temp មិនមានទៀតទេ។ សូមចុច Start ហើយ Upload វីដេអូ + SRT ម្តងទៀត។"
            )
        )
        buttons = []
        if retry_allowed:
            buttons.append([InlineKeyboardButton("🔄 ព្យាយាមម្តងទៀត", callback_data=f"dubbing:retry:{task_id}")])
        buttons.append([InlineKeyboardButton("Start", callback_data="start_dubbing")])
        markup = InlineKeyboardMarkup(buttons)
        if chat_id:
            try:
                if message_id:
                    await application.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)
                else:
                    await application.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
            except TelegramError as exc:
                logger.warning("Could not notify user about interrupted task %s: %s", task_id, exc)
        recovered += 1
    if recovered:
        logger.warning("Recovered %s interrupted processing task(s) after startup", recovered)


async def _post_init(application: Application) -> None:
    check_ffmpeg_available()
    if settings.cleanup_on_start:
        deleted = clean_temp_older_than(hours=settings.cleanup_old_temp_hours)
        logger.info("Startup temp cleanup deleted %s old files", deleted)

    redis_ok = await redis_service.ping()
    supabase_ok = await supabase_service.health_check()
    logger.info("Startup checks | Redis=%s | Supabase=%s", redis_ok, supabase_ok)
    if not redis_ok:
        raise RuntimeError("Redis connection failed")
    if not supabase_ok:
        message = (
            "Supabase connection failed. Check SUPABASE_URL and SUPABASE_SERVICE_KEY, "
            "then run database/supabase_schema.sql in Supabase SQL Editor."
        )
        if settings.allow_start_without_supabase:
            logger.warning("%s Starting anyway because ALLOW_START_WITHOUT_SUPABASE=true", message)
        else:
            raise RuntimeError(message)

    runtime = await runtime_settings.load(force=True)
    if bool(runtime.get("clear_stale_queue_on_start", settings.clear_stale_queue_on_start)):
        purged = await redis_service.purge_queue()
        if purged:
            logger.warning("Startup cleared %s stale Redis queue job(s). Local temp files are not durable across Render restarts.", purged)

    await _recover_interrupted_processing_tasks(application)

    application.bot_data["health_server"] = start_health_server()

    if bool(runtime.get("in_process_worker", settings.in_process_worker)):
        from app.workers.dubbing_worker import worker_loop

        worker_tasks = []
        worker_count = max(1, min(4, int(runtime.get("in_process_worker_count", settings.in_process_worker_count))))
        for index in range(worker_count):
            task = asyncio.create_task(worker_loop(application.bot, name=f"in-process-dubbing-worker-{index + 1}"))
            worker_tasks.append(task)
        application.bot_data["worker_tasks"] = worker_tasks
        logger.info("Started %s in-process dubbing worker(s)", len(worker_tasks))


async def _post_shutdown(application: Application) -> None:
    worker_tasks = application.bot_data.get("worker_tasks", [])
    for task in worker_tasks:
        task.cancel()
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    stop_health_server(application.bot_data.get("health_server"))
    await redis_service.close()
    logger.info("Bot shutdown complete")


async def text_router(update, context) -> None:
    if await handle_admin_text(update, context):
        return
    await update.effective_message.reply_text("សូមចុច /start ដើម្បីចាប់ផ្តើម ឬ /help ដើម្បីមើលរបៀបប្រើ។")


def build_application() -> Application:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required in .env")

    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .concurrent_updates(True)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(home_callback, pattern="^start_home$"))
    application.add_handler(CallbackQueryHandler(help_callback, pattern="^start_help$"))
    application.add_handler(CallbackQueryHandler(start_dubbing_callback, pattern="^start_dubbing$"))
    application.add_handler(CallbackQueryHandler(voice_callback, pattern="^voice:"))
    application.add_handler(CallbackQueryHandler(dubbing_callback, pattern="^dubbing:"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin:"))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, handle_video_or_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.add_error_handler(error_handler)
    return application
