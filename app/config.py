"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return int(value) if value is not None and value.strip() != "" else default
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    try:
        return float(value) if value is not None and value.strip() != "" else default
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


def _safe_tts_provider() -> str:
    value = os.getenv("TTS_PROVIDER", "edge").strip().lower()
    return value if value in {"edge", "azure", "auto"} else "edge"


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: List[int]
    supabase_url: str
    supabase_service_key: str
    redis_url: str
    allow_start_without_supabase: bool

    max_video_duration_seconds: int
    max_video_size_mb: int
    max_srt_size_mb: int
    max_subtitle_chars: int
    min_subtitle_duration_seconds: float

    keep_original_audio: bool
    original_audio_volume: float
    dubbed_audio_volume: float
    normalize_audio: bool

    temp_dir: Path
    videos_dir: Path
    subtitles_dir: Path
    audio_dir: Path
    output_dir: Path
    tts_cache_dir: Path
    logs_dir: Path
    app_log_file: Path

    redis_queue_key: str
    redis_socket_timeout_seconds: float
    bot_instance_lock_enabled: bool
    bot_instance_lock_key: str
    bot_instance_lock_ttl_seconds: int
    bot_instance_lock_refresh_seconds: int
    task_ttl_seconds: int
    task_lock_ttl_seconds: int
    worker_queue_timeout_seconds: int
    clean_success_files: bool
    keep_failed_files: bool
    cleanup_on_start: bool
    cleanup_old_temp_hours: int
    clear_stale_queue_on_start: bool

    tts_provider: str
    tts_cache_enabled: bool
    tts_max_retries: int
    tts_retry_base_delay_seconds: float
    edge_tts_rate: str
    edge_tts_volume: str
    edge_tts_pitch: str
    edge_tts_proxy: str
    edge_tts_delay_seconds: float
    edge_tts_connect_timeout: int
    edge_tts_receive_timeout: int
    azure_speech_key: str
    azure_speech_region: str
    azure_output_format: str
    azure_user_agent: str
    azure_tts_timeout_seconds: float

    telegram_send_max_retries: int
    telegram_broadcast_delay_seconds: float
    progress_edit_interval_seconds: float
    progress_min_delta_percent: int

    ffmpeg_binary: str
    ffprobe_binary: str
    ffmpeg_preset: str
    ffmpeg_video_crf: int
    ffmpeg_merge_timeout_seconds: int

    in_process_worker: bool
    in_process_worker_count: int
    enable_health_server: bool
    health_server_host: str
    health_server_port: int
    drop_pending_updates: bool

    @property
    def max_video_size_bytes(self) -> int:
        return self.max_video_size_mb * 1024 * 1024

    @property
    def max_srt_size_bytes(self) -> int:
        return self.max_srt_size_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for folder in [
            self.temp_dir,
            self.videos_dir,
            self.subtitles_dir,
            self.audio_dir,
            self.output_dir,
            self.tts_cache_dir,
            self.logs_dir,
        ]:
            folder.mkdir(parents=True, exist_ok=True)
        self.app_log_file.touch(exist_ok=True)


settings = Settings(
    bot_token=os.getenv("BOT_TOKEN", "").strip(),
    admin_ids=_get_admin_ids(),
    supabase_url=os.getenv("SUPABASE_URL", "").strip(),
    supabase_service_key=os.getenv("SUPABASE_SERVICE_KEY", "").strip(),
    redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0").strip(),
    allow_start_without_supabase=_get_bool("ALLOW_START_WITHOUT_SUPABASE", False),
    max_video_duration_seconds=_get_int("MAX_VIDEO_DURATION_SECONDS", 60),
    max_video_size_mb=_get_int("MAX_VIDEO_SIZE_MB", 50),
    max_srt_size_mb=_get_int("MAX_SRT_SIZE_MB", 2),
    max_subtitle_chars=_get_int("MAX_SUBTITLE_CHARS", 450),
    min_subtitle_duration_seconds=_get_float("MIN_SUBTITLE_DURATION_SECONDS", 0.20),
    keep_original_audio=_get_bool("KEEP_ORIGINAL_AUDIO", False),
    original_audio_volume=_get_float("ORIGINAL_AUDIO_VOLUME", 0.15),
    dubbed_audio_volume=_get_float("DUBBED_AUDIO_VOLUME", 1.0),
    normalize_audio=_get_bool("NORMALIZE_AUDIO", True),
    temp_dir=Path(os.getenv("TEMP_DIR", str(PROJECT_DIR / "temp"))),
    videos_dir=Path(os.getenv("VIDEOS_DIR", str(PROJECT_DIR / "temp" / "videos"))),
    subtitles_dir=Path(os.getenv("SUBTITLES_DIR", str(PROJECT_DIR / "temp" / "subtitles"))),
    audio_dir=Path(os.getenv("AUDIO_DIR", str(PROJECT_DIR / "temp" / "audio"))),
    output_dir=Path(os.getenv("OUTPUT_DIR", str(PROJECT_DIR / "temp" / "output"))),
    tts_cache_dir=Path(os.getenv("TTS_CACHE_DIR", str(PROJECT_DIR / "temp" / "tts_cache"))),
    logs_dir=Path(os.getenv("LOGS_DIR", str(PROJECT_DIR / "logs"))),
    app_log_file=Path(os.getenv("APP_LOG_FILE", str(PROJECT_DIR / "logs" / "app.log"))),
    redis_queue_key=os.getenv("REDIS_QUEUE_KEY", "queue:dubbing").strip(),
    redis_socket_timeout_seconds=_get_float("REDIS_SOCKET_TIMEOUT_SECONDS", 10.0),
    bot_instance_lock_enabled=_get_bool("BOT_INSTANCE_LOCK_ENABLED", True),
    bot_instance_lock_key=os.getenv("BOT_INSTANCE_LOCK_KEY", "bot:polling:instance_lock").strip(),
    bot_instance_lock_ttl_seconds=max(30, _get_int("BOT_INSTANCE_LOCK_TTL_SECONDS", 90)),
    bot_instance_lock_refresh_seconds=max(10, _get_int("BOT_INSTANCE_LOCK_REFRESH_SECONDS", 25)),
    task_ttl_seconds=_get_int("TASK_TTL_SECONDS", 60 * 60 * 24),
    task_lock_ttl_seconds=_get_int("TASK_LOCK_TTL_SECONDS", 60 * 30),
    worker_queue_timeout_seconds=_get_int("WORKER_QUEUE_TIMEOUT_SECONDS", 2),
    clean_success_files=_get_bool("CLEAN_SUCCESS_FILES", True),
    keep_failed_files=_get_bool("KEEP_FAILED_FILES", True),
    cleanup_on_start=_get_bool("CLEANUP_ON_START", False),
    cleanup_old_temp_hours=_get_int("CLEANUP_OLD_TEMP_HOURS", 24),
    clear_stale_queue_on_start=_get_bool("CLEAR_STALE_QUEUE_ON_START", True),
    tts_provider=_safe_tts_provider(),
    tts_cache_enabled=_get_bool("TTS_CACHE_ENABLED", True),
    tts_max_retries=max(1, _get_int("TTS_MAX_RETRIES", 3)),
    tts_retry_base_delay_seconds=_get_float("TTS_RETRY_BASE_DELAY_SECONDS", 3.0),
    edge_tts_rate=os.getenv("EDGE_TTS_RATE", "+0%"),
    edge_tts_volume=os.getenv("EDGE_TTS_VOLUME", "+0%"),
    edge_tts_pitch=os.getenv("EDGE_TTS_PITCH", "+0Hz"),
    edge_tts_proxy=os.getenv("EDGE_TTS_PROXY", "").strip(),
    edge_tts_delay_seconds=_get_float("EDGE_TTS_DELAY_SECONDS", 6.0),
    edge_tts_connect_timeout=_get_int("EDGE_TTS_CONNECT_TIMEOUT", 15),
    edge_tts_receive_timeout=_get_int("EDGE_TTS_RECEIVE_TIMEOUT", 60),
    azure_speech_key=os.getenv("AZURE_SPEECH_KEY", "").strip(),
    azure_speech_region=os.getenv("AZURE_SPEECH_REGION", "").strip(),
    azure_output_format=os.getenv("AZURE_OUTPUT_FORMAT", "audio-24khz-48kbitrate-mono-mp3").strip(),
    azure_user_agent=os.getenv("AZURE_USER_AGENT", "ai-dubbing-bot").strip(),
    azure_tts_timeout_seconds=_get_float("AZURE_TTS_TIMEOUT_SECONDS", 60.0),
    telegram_send_max_retries=max(1, _get_int("TELEGRAM_SEND_MAX_RETRIES", 3)),
    telegram_broadcast_delay_seconds=_get_float("TELEGRAM_BROADCAST_DELAY_SECONDS", 0.05),
    progress_edit_interval_seconds=_get_float("PROGRESS_EDIT_INTERVAL_SECONDS", 2.0),
    progress_min_delta_percent=max(1, _get_int("PROGRESS_MIN_DELTA_PERCENT", 3)),
    ffmpeg_binary=os.getenv("FFMPEG_BINARY", "ffmpeg").strip(),
    ffprobe_binary=os.getenv("FFPROBE_BINARY", "ffprobe").strip(),
    ffmpeg_preset=os.getenv("FFMPEG_PRESET", "veryfast").strip(),
    ffmpeg_video_crf=_get_int("FFMPEG_VIDEO_CRF", 23),
    ffmpeg_merge_timeout_seconds=max(120, _get_int("FFMPEG_MERGE_TIMEOUT_SECONDS", 420)),
    in_process_worker=_get_bool("IN_PROCESS_WORKER", True),
    in_process_worker_count=max(1, _get_int("IN_PROCESS_WORKER_COUNT", 1)),
    enable_health_server=_get_bool("ENABLE_HEALTH_SERVER", bool(os.getenv("PORT"))),
    health_server_host=os.getenv("HEALTH_SERVER_HOST", "0.0.0.0").strip(),
    health_server_port=_get_int("PORT", _get_int("HEALTH_SERVER_PORT", 10000)),
    drop_pending_updates=_get_bool("DROP_PENDING_UPDATES", False),
)
