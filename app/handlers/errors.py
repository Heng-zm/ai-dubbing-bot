"""Global error handler."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.services.logger_service import log_db, logger


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception", exc_info=context.error)
    await log_db(
        "error",
        "telegram_update",
        "Unhandled exception while processing update",
        {"error": str(context.error), "update": str(update)[:1000]},
    )
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "សូមទោស មានបញ្ហាបន្តិចក្នុងការដំណើរការ។\n\n"
                "សូមព្យាយាមម្តងទៀត ឬចុច /start ដើម្បីចាប់ផ្តើមថ្មី។"
            )
        except Exception:
            pass
