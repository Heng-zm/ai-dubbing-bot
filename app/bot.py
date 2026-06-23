"""Telegram Application factory."""

from __future__ import annotations

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app.config import settings
from app.handlers.admin import admin_callback, admin_command, handle_admin_text
from app.handlers.dubbing import handle_video_or_document
from app.handlers.errors import error_handler
from app.handlers.start import start_command, start_dubbing_callback, voice_callback
from app.services.logger_service import logger
from app.services.redis_service import redis_service
from app.services.supabase_service import supabase_service
from app.utils.file_utils import check_ffmpeg_available


async def _post_init(application: Application) -> None:
    check_ffmpeg_available()
    redis_ok = await redis_service.ping()
    supabase_ok = await supabase_service.health_check()
    logger.info("Startup checks | Redis=%s | Supabase=%s", redis_ok, supabase_ok)
    if not redis_ok:
        raise RuntimeError("Redis connection failed")
    if not supabase_ok:
        raise RuntimeError("Supabase connection failed. Run database/supabase_schema.sql and check .env")


async def _post_shutdown(application: Application) -> None:
    await redis_service.close()
    logger.info("Bot shutdown complete")


async def text_router(update, context) -> None:
    # Admin broadcast text must be handled before normal fallback.
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
    application.add_handler(CallbackQueryHandler(start_dubbing_callback, pattern="^start_dubbing$"))
    application.add_handler(CallbackQueryHandler(voice_callback, pattern="^voice:"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin:"))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, handle_video_or_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.add_error_handler(error_handler)
    return application
