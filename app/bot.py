"""Telegram Application factory."""

from __future__ import annotations

import asyncio

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app.config import settings
from app.handlers.admin import admin_callback, admin_command, handle_admin_text
from app.handlers.dubbing import cancel_command, dubbing_callback, handle_video_or_document, status_command
from app.handlers.errors import error_handler
from app.handlers.start import start_command, start_dubbing_callback, voice_callback
from app.services.health_server import start_health_server, stop_health_server
from app.services.logger_service import logger
from app.services.redis_service import redis_service
from app.services.supabase_service import supabase_service
from app.utils.file_utils import check_ffmpeg_available, clean_temp_older_than


async def _post_init(application: Application) -> None:
    check_ffmpeg_available()
    if settings.cleanup_on_start:
        deleted = clean_temp_older_than(hours=settings.cleanup_old_temp_hours)
        logger.info("Startup temp cleanup deleted %s old files", deleted)

    redis_ok = await redis_service.ping()
    if redis_ok and settings.clear_stale_queue_on_start:
        purged = await redis_service.purge_queue()
        if purged:
            logger.warning("Startup cleared %s stale Redis queue job(s). Local temp files are not durable across Render restarts.", purged)
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

    application.bot_data["health_server"] = start_health_server()

    if settings.in_process_worker:
        from app.workers.dubbing_worker import worker_loop

        worker_tasks = []
        for index in range(settings.in_process_worker_count):
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
    await update.effective_message.reply_text("សូមចុច /start ដើម្បីចាប់ផ្តើមប្រើ Bot។")


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
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(start_dubbing_callback, pattern="^start_dubbing$"))
    application.add_handler(CallbackQueryHandler(voice_callback, pattern="^voice:"))
    application.add_handler(CallbackQueryHandler(dubbing_callback, pattern="^dubbing:"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin:"))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, handle_video_or_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.add_error_handler(error_handler)
    return application
