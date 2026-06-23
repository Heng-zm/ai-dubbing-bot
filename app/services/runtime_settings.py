"""Runtime bot settings editable from Telegram admin dashboard.

Secrets and infrastructure values still come from .env:
BOT_TOKEN, ADMIN_IDS, SUPABASE_URL, SUPABASE_SERVICE_KEY, REDIS_URL, health server.
Operational values are stored in Supabase table public.bot_settings and cached in Redis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Literal, Optional

from app.config import settings

SettingType = Literal["int", "float", "bool", "str", "choice"]


@dataclass(frozen=True)
class SettingDef:
    key: str
    label: str
    type: SettingType
    default: Any
    description: str = ""
    min_value: float | None = None
    max_value: float | None = None
    choices: tuple[str, ...] = ()
    restart_required: bool = False
    editable: bool = True


SETTING_DEFS: dict[str, SettingDef] = {
    "max_video_duration_seconds": SettingDef(
        "max_video_duration_seconds", "Max video duration", "int", settings.max_video_duration_seconds, "seconds", 5, 300
    ),
    "max_video_size_mb": SettingDef("max_video_size_mb", "Max video size", "int", settings.max_video_size_mb, "MB", 1, 2000),
    "max_srt_size_mb": SettingDef("max_srt_size_mb", "Max SRT size", "int", settings.max_srt_size_mb, "MB", 1, 50),
    "tts_provider": SettingDef(
        "tts_provider", "TTS provider", "choice", settings.tts_provider, "edge / auto / azure", choices=("edge", "auto", "azure")
    ),
    "tts_cache_enabled": SettingDef("tts_cache_enabled", "TTS cache", "bool", settings.tts_cache_enabled),
    "keep_original_audio": SettingDef("keep_original_audio", "Keep original audio", "bool", settings.keep_original_audio),
    "original_audio_volume": SettingDef("original_audio_volume", "Original audio volume", "float", settings.original_audio_volume, "0.0 - 1.0", 0.0, 1.0),
    "dubbed_audio_volume": SettingDef("dubbed_audio_volume", "Dubbed audio volume", "float", settings.dubbed_audio_volume, "0.0 - 3.0", 0.0, 3.0),
    "in_process_worker": SettingDef("in_process_worker", "In-process worker", "bool", settings.in_process_worker, restart_required=True),
    "in_process_worker_count": SettingDef("in_process_worker_count", "Worker count", "int", settings.in_process_worker_count, "requires restart", 1, 4, restart_required=True),
    "clean_success_files": SettingDef("clean_success_files", "Clean success files", "bool", settings.clean_success_files),
    "keep_failed_files": SettingDef("keep_failed_files", "Keep failed files", "bool", settings.keep_failed_files),
    "clear_stale_queue_on_start": SettingDef("clear_stale_queue_on_start", "Clear queue on startup", "bool", settings.clear_stale_queue_on_start, restart_required=True),
    "redis_queue_key": SettingDef("redis_queue_key", "Queue key", "str", settings.redis_queue_key, "requires restart if changed", restart_required=True),
    "watermark_enabled": SettingDef("watermark_enabled", "Watermark", "bool", True),
    "watermark_text": SettingDef("watermark_text", "Watermark text", "str", "Dubbed by @aidubbingkhbot", "shown on output video"),
    "watermark_position": SettingDef(
        "watermark_position", "Watermark position", "choice", "bottom_right", choices=("bottom_right", "bottom_left", "top_right", "top_left")
    ),
    "multi_voice_enabled": SettingDef("multi_voice_enabled", "Multi voice per character", "bool", True),
    "show_processing_estimate": SettingDef("show_processing_estimate", "Processing time estimate", "bool", True),
}

CACHE_KEY = "bot:runtime_settings"
CACHE_TTL_SECONDS = 60
_memory_cache: dict[str, Any] = {}


def _defaults() -> dict[str, Any]:
    return {key: definition.default for key, definition in SETTING_DEFS.items()}


def _cast_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _cast_value(definition: SettingDef, value: Any) -> Any:
    if value is None or value == "":
        return definition.default
    if definition.type == "bool":
        return _cast_bool(value)
    if definition.type == "int":
        casted = int(float(str(value).strip()))
        if definition.min_value is not None:
            casted = max(int(definition.min_value), casted)
        if definition.max_value is not None:
            casted = min(int(definition.max_value), casted)
        return casted
    if definition.type == "float":
        casted = float(str(value).strip())
        if definition.min_value is not None:
            casted = max(float(definition.min_value), casted)
        if definition.max_value is not None:
            casted = min(float(definition.max_value), casted)
        return casted
    if definition.type == "choice":
        casted = str(value).strip().lower()
        if casted not in definition.choices:
            raise ValueError(f"Allowed values: {', '.join(definition.choices)}")
        return casted
    return str(value).strip()


def validate_setting_value(key: str, raw_value: str) -> Any:
    definition = SETTING_DEFS.get(key)
    if not definition or not definition.editable:
        raise ValueError("Unknown setting")
    value = _cast_value(definition, raw_value)
    if definition.type == "str" and definition.key == "redis_queue_key":
        if not value or any(ch.isspace() for ch in value):
            raise ValueError("Queue key must not be empty or contain spaces")
    return value


def serialize_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def display_value(key: str, value: Any) -> str:
    definition = SETTING_DEFS.get(key)
    if definition and definition.type == "bool":
        return "True" if _cast_bool(value) else "False"
    if definition and definition.type == "float":
        try:
            return f"{float(value):.2f}".rstrip("0").rstrip(".")
        except Exception:
            return str(value)
    return str(value)


class RuntimeSettingsService:
    async def load(self, force: bool = False) -> dict[str, Any]:
        """Load runtime settings from Redis cache or Supabase, falling back to defaults."""
        global _memory_cache
        if _memory_cache and not force:
            return dict(_memory_cache)

        data = _defaults()
        if not force:
            try:
                from app.services.redis_service import redis_service

                cached = await redis_service.get(CACHE_KEY)
                if cached:
                    decoded = json.loads(cached)
                    if isinstance(decoded, dict):
                        data.update(self._coerce_many(decoded))
                        _memory_cache = data
                        return dict(data)
            except Exception:
                pass

        try:
            from app.services.supabase_service import supabase_service

            rows = await supabase_service.get_bot_settings()
            data.update(self._coerce_many(rows))
        except Exception:
            # Database may be temporarily unavailable during startup. Defaults keep bot alive.
            pass

        _memory_cache = data
        try:
            from app.services.redis_service import redis_service

            await redis_service.set(CACHE_KEY, json.dumps(data, ensure_ascii=False), ex=CACHE_TTL_SECONDS)
        except Exception:
            pass
        return dict(data)

    def cached(self) -> dict[str, Any]:
        data = _defaults()
        data.update(_memory_cache)
        return data

    def _coerce_many(self, raw: dict[str, Any]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, definition in SETTING_DEFS.items():
            if key not in raw:
                continue
            try:
                output[key] = _cast_value(definition, raw[key])
            except Exception:
                output[key] = definition.default
        return output

    async def get(self, key: str) -> Any:
        values = await self.load()
        return values.get(key, SETTING_DEFS[key].default)

    async def get_int(self, key: str) -> int:
        return int(await self.get(key))

    async def get_float(self, key: str) -> float:
        return float(await self.get(key))

    async def get_bool(self, key: str) -> bool:
        return _cast_bool(await self.get(key))

    async def get_str(self, key: str) -> str:
        return str(await self.get(key))

    async def set_value(self, key: str, raw_value: str, admin_telegram_id: int) -> tuple[Any, SettingDef]:
        definition = SETTING_DEFS.get(key)
        if not definition:
            raise ValueError("Unknown setting")
        value = validate_setting_value(key, raw_value)
        from app.services.supabase_service import supabase_service
        from app.services.redis_service import redis_service

        await supabase_service.upsert_bot_setting(
            key=key,
            value=serialize_value(value),
            value_type=definition.type,
            admin_telegram_id=admin_telegram_id,
        )
        # Refresh memory/cache immediately.
        current = self.cached()
        current[key] = value
        global _memory_cache
        _memory_cache = current
        await redis_service.set(CACHE_KEY, json.dumps(current, ensure_ascii=False), ex=CACHE_TTL_SECONDS)
        return value, definition

    async def reset_value(self, key: str) -> SettingDef:
        definition = SETTING_DEFS.get(key)
        if not definition:
            raise ValueError("Unknown setting")
        from app.services.supabase_service import supabase_service
        from app.services.redis_service import redis_service

        await supabase_service.delete_bot_setting(key)
        current = self.cached()
        current[key] = definition.default
        global _memory_cache
        _memory_cache = current
        await redis_service.set(CACHE_KEY, json.dumps(current, ensure_ascii=False), ex=CACHE_TTL_SECONDS)
        return definition


runtime_settings = RuntimeSettingsService()
