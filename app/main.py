"""Bot entrypoint."""

from __future__ import annotations

from app.bot import build_application
from app.config import settings
from app.services.logger_service import logger
from app.services.health_server import start_health_server


def main() -> None:
    settings.ensure_dirs()
    # Start the tiny health server before Telegram/Redis/Supabase checks so Render Web Service
    # immediately detects an open port on 0.0.0.0:$PORT. This is safe and idempotent.
    start_health_server()
    application = build_application()
    logger.info("Starting AI Dubbing Bot with polling")
    application.run_polling(
        allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"],
        close_loop=False,
        drop_pending_updates=settings.drop_pending_updates,
    )


if __name__ == "__main__":
    main()
