"""Logging setup and database log helper."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

from app.config import settings


def setup_logging() -> logging.Logger:
    settings.ensure_dirs()
    logger = logging.getLogger("ai_dubbing_bot")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        settings.app_log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


logger = setup_logging()


async def log_db(level: str, category: str, message: str, metadata: Dict[str, Any] | None = None) -> None:
    """Write a log entry to Supabase if configured; local logging always remains primary."""
    metadata = metadata or {}
    if level.lower() == "error":
        logger.error("%s | %s | %s", category, message, json.dumps(metadata, ensure_ascii=False))
    elif level.lower() == "warning":
        logger.warning("%s | %s | %s", category, message, json.dumps(metadata, ensure_ascii=False))
    else:
        logger.info("%s | %s | %s", category, message, json.dumps(metadata, ensure_ascii=False))

    try:
        from app.services.supabase_service import supabase_service

        await supabase_service.create_log(level=level, category=category, message=message, metadata=metadata)
    except Exception:
        # Never let remote logging break bot execution.
        return
