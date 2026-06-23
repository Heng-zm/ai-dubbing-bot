"""Application configuration loaded from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return int(value) if value is not None and value != "" else default
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    try:
        return float(value) if value is not None and value != "" else default
    except ValueError:
        return default


def _get_admin_ids() -> List[int]:
    raw = os.getenv("ADMIN_IDS", "")
    ids: List[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.append(int(item))
        except ValueError:
            continue
    return ids


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: List[int]
    supabase_url: str
    supabase_service_key: str
    redis_url: str

    max_video_duration_seconds: int
    max_video_size_mb: int
    keep_original_audio: bool
    original_audio_volume: float
    dubbed_audio_volume: float

    temp_dir: Path
    videos_dir: Path
    subtitles_dir: Path
    audio_dir: Path
    output_dir: Path
    logs_dir: Path
    app_log_file: Path

    redis_queue_key: str
    clean_success_files: bool
    keep_failed_files: bool
    tts_max_retries: int
    telegram_send_max_retries: int
    progress_edit_interval_seconds: float
    ffmpeg_binary: str
    ffprobe_binary: str
    in_process_worker: bool
    in_process_worker_count: int
    enable_health_server: bool
    health_server_host: str
    health_server_port: int

    @property
    def max_video_size_bytes(self) -> int:
        return self.max_video_size_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for folder in [
            self.temp_dir,
            self.videos_dir,
            self.subtitles_dir,
            self.audio_dir,
            self.output_dir,
            self.logs_dir,
        ]:
            folder.mkdir(parents=True, exist_ok=True)
        self.app_log_file.touch(exist_ok=True)


settings = Settings(
    bot_token=os.getenv("BOT_TOKEN", ""),
    admin_ids=_get_admin_ids(),
    supabase_url=os.getenv("SUPABASE_URL", ""),
    supabase_service_key=os.getenv("SUPABASE_SERVICE_KEY", ""),
    redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    max_video_duration_seconds=_get_int("MAX_VIDEO_DURATION_SECONDS", 60),
    max_video_size_mb=_get_int("MAX_VIDEO_SIZE_MB", 50),
    keep_original_audio=_get_bool("KEEP_ORIGINAL_AUDIO", False),
    original_audio_volume=_get_float("ORIGINAL_AUDIO_VOLUME", 0.15),
    dubbed_audio_volume=_get_float("DUBBED_AUDIO_VOLUME", 1.0),
    temp_dir=Path(os.getenv("TEMP_DIR", str(PROJECT_DIR / "temp"))),
    videos_dir=Path(os.getenv("VIDEOS_DIR", str(PROJECT_DIR / "temp" / "videos"))),
    subtitles_dir=Path(os.getenv("SUBTITLES_DIR", str(PROJECT_DIR / "temp" / "subtitles"))),
    audio_dir=Path(os.getenv("AUDIO_DIR", str(PROJECT_DIR / "temp" / "audio"))),
    output_dir=Path(os.getenv("OUTPUT_DIR", str(PROJECT_DIR / "temp" / "output"))),
    logs_dir=Path(os.getenv("LOGS_DIR", str(PROJECT_DIR / "logs"))),
    app_log_file=Path(os.getenv("APP_LOG_FILE", str(PROJECT_DIR / "logs" / "app.log"))),
    redis_queue_key=os.getenv("REDIS_QUEUE_KEY", "queue:dubbing"),
    clean_success_files=_get_bool("CLEAN_SUCCESS_FILES", True),
    keep_failed_files=_get_bool("KEEP_FAILED_FILES", True),
    tts_max_retries=_get_int("TTS_MAX_RETRIES", 3),
    telegram_send_max_retries=_get_int("TELEGRAM_SEND_MAX_RETRIES", 3),
    progress_edit_interval_seconds=_get_float("PROGRESS_EDIT_INTERVAL_SECONDS", 2.0),
    ffmpeg_binary=os.getenv("FFMPEG_BINARY", "ffmpeg"),
    ffprobe_binary=os.getenv("FFPROBE_BINARY", "ffprobe"),
    in_process_worker=_get_bool("IN_PROCESS_WORKER", True),
    in_process_worker_count=max(1, _get_int("IN_PROCESS_WORKER_COUNT", 1)),
    enable_health_server=_get_bool("ENABLE_HEALTH_SERVER", False),
    health_server_host=os.getenv("HEALTH_SERVER_HOST", "0.0.0.0"),
    health_server_port=_get_int("PORT", _get_int("HEALTH_SERVER_PORT", 10000)),
)
