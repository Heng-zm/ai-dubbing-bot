"""Bot entrypoint."""

from __future__ import annotations

from app.bot import build_application
from app.config import settings
from app.services.logger_service import logger


def main() -> None:
    settings.ensure_dirs()
    application = build_application()
    logger.info("Starting AI Dubbing Bot with polling")
    application.run_polling(
        allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"],
        close_loop=False,
    )


if __name__ == "__main__":
    main()
