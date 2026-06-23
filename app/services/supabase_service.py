"""Supabase database service.

The Supabase Python client is synchronous, so this service wraps calls in
asyncio.to_thread when used from async Telegram handlers.

Production note:
PostgREST/Supabase can temporarily keep an old schema cache after a migration.
If the deployed code sends a newly added column, Supabase may return PGRST204.
The write helpers below retry without optional columns such as updated_at so the
bot keeps running while the database migration/schema cache is being fixed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_missing_column_error(exc: Exception, column: str) -> bool:
    """Detect Supabase/PostgREST PGRST204 stale/missing-column errors.

    supabase-py raises postgrest.exceptions.APIError and its payload is not
    stable across versions, so string matching is intentionally defensive.
    """
    text = str(exc)
    return "PGRST204" in text and f"'{column}'" in text and "column" in text


def _without_keys(payload: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    clone = dict(payload)
    for key in keys:
        clone.pop(key, None)
    return clone


class SupabaseService:
    def __init__(self) -> None:
        self.client = None
        self._warned_missing_updated_at_tables: set[str] = set()

    def connect_sync(self):
        if self.client is None:
            if not settings.supabase_url or not settings.supabase_service_key:
                raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
            from supabase import create_client

            self.client = create_client(settings.supabase_url, settings.supabase_service_key)
        return self.client

    def _warn_missing_optional_column_once(self, table: str, column: str, exc: Exception) -> None:
        """Log once when a safe fallback is used for an optional schema column."""
        key = f"{table}.{column}"
        if key in self._warned_missing_updated_at_tables:
            return
        self._warned_missing_updated_at_tables.add(key)
        from app.services.logger_service import logger

        logger.warning(
            "Supabase table %s is missing optional column %s or schema cache is stale. "
            "Retrying write without it. Run database/migrations/001_add_updated_at_and_reload_cache.sql. Error: %s",
            table,
            column,
            exc,
        )

    async def health_check(self) -> bool:
        """Return True when credentials and required tables are usable."""
        from app.services.logger_service import logger

        def _check() -> bool:
            client = self.connect_sync()
            for table in ("users", "dubbing_tasks", "broadcasts", "logs"):
                client.table(table).select("id", count="exact").limit(1).execute()
            return True

        try:
            return await asyncio.to_thread(_check)
        except Exception as exc:
            logger.exception("Supabase health check failed: %s", exc)
            return False

    async def upsert_user(self, user, selected_voice: str | None = None) -> Optional[Dict[str, Any]]:
        now_iso = _now_iso()
        base_payload = {
            "telegram_user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "language_code": user.language_code,
            "last_active_at": now_iso,
        }
        if selected_voice:
            base_payload["selected_voice"] = selected_voice
        payload = dict(base_payload)
        payload["updated_at"] = now_iso

        def _run():
            client = self.connect_sync()
            try:
                result = client.table("users").upsert(payload, on_conflict="telegram_user_id").execute()
            except Exception as exc:
                if not _is_missing_column_error(exc, "updated_at"):
                    raise
                self._warn_missing_optional_column_once("users", "updated_at", exc)
                result = client.table("users").upsert(base_payload, on_conflict="telegram_user_id").execute()
            rows = result.data or []
            if rows:
                return rows[0]
            fallback = client.table("users").select("*").eq("telegram_user_id", user.id).limit(1).execute()
            return (fallback.data or [None])[0]

        return await asyncio.to_thread(_run)

    async def update_user_voice(self, telegram_user_id: int, voice: str) -> None:
        payload = {"selected_voice": voice, "updated_at": _now_iso()}

        def _run() -> None:
            client = self.connect_sync()
            try:
                client.table("users").update(payload).eq("telegram_user_id", telegram_user_id).execute()
            except Exception as exc:
                if not _is_missing_column_error(exc, "updated_at"):
                    raise
                self._warn_missing_optional_column_once("users", "updated_at", exc)
                client.table("users").update(_without_keys(payload, "updated_at")).eq(
                    "telegram_user_id", telegram_user_id
                ).execute()

        await asyncio.to_thread(_run)

    async def create_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        def _run():
            client = self.connect_sync()
            result = client.table("dubbing_tasks").insert(payload).execute()
            return (result.data or [{}])[0]

        return await asyncio.to_thread(_run)

    async def update_task(self, task_id: str, payload: Dict[str, Any]) -> None:
        clean_payload = dict(payload)
        clean_payload["updated_at"] = _now_iso()

        def _run() -> None:
            client = self.connect_sync()
            try:
                client.table("dubbing_tasks").update(clean_payload).eq("id", task_id).execute()
            except Exception as exc:
                if not _is_missing_column_error(exc, "updated_at"):
                    raise
                self._warn_missing_optional_column_once("dubbing_tasks", "updated_at", exc)
                client.table("dubbing_tasks").update(_without_keys(clean_payload, "updated_at")).eq(
                    "id", task_id
                ).execute()

        await asyncio.to_thread(_run)

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        def _run():
            client = self.connect_sync()
            result = client.table("dubbing_tasks").select("*").eq("id", task_id).limit(1).execute()
            return (result.data or [None])[0]

        return await asyncio.to_thread(_run)

    async def list_users(self, limit: int = 20) -> List[Dict[str, Any]]:
        def _run():
            client = self.connect_sync()
            result = client.table("users").select("*").order("last_active_at", desc=True).limit(limit).execute()
            return result.data or []

        return await asyncio.to_thread(_run)

    async def list_all_telegram_user_ids(self) -> List[int]:
        def _run():
            client = self.connect_sync()
            rows: List[Dict[str, Any]] = []
            start = 0
            page_size = 1000
            while True:
                result = client.table("users").select("telegram_user_id").range(start, start + page_size - 1).execute()
                batch = result.data or []
                rows.extend(batch)
                if len(batch) < page_size:
                    break
                start += page_size
            seen: set[int] = set()
            output: List[int] = []
            for row in rows:
                value = row.get("telegram_user_id")
                if value is None:
                    continue
                user_id = int(value)
                if user_id not in seen:
                    seen.add(user_id)
                    output.append(user_id)
            return output

        return await asyncio.to_thread(_run)

    async def list_tasks(self, status: str | None = None, limit: int = 20) -> List[Dict[str, Any]]:
        def _run():
            client = self.connect_sync()
            query = client.table("dubbing_tasks").select("*")
            if status:
                query = query.eq("status", status)
            result = query.order("created_at", desc=True).limit(limit).execute()
            return result.data or []

        return await asyncio.to_thread(_run)

    async def create_broadcast_log(self, payload: Dict[str, Any]) -> None:
        def _run() -> None:
            client = self.connect_sync()
            client.table("broadcasts").insert(payload).execute()

        await asyncio.to_thread(_run)

    async def create_log(self, level: str, category: str, message: str, metadata: Dict[str, Any] | None = None) -> None:
        def _run() -> None:
            client = self.connect_sync()
            client.table("logs").insert(
                {
                    "level": level,
                    "category": category,
                    "message": message,
                    "metadata": metadata or {},
                }
            ).execute()

        await asyncio.to_thread(_run)

    async def recent_logs(self, limit: int = 20) -> List[Dict[str, Any]]:
        def _run():
            client = self.connect_sync()
            result = client.table("logs").select("*").order("created_at", desc=True).limit(limit).execute()
            return result.data or []

        return await asyncio.to_thread(_run)



    async def get_bot_settings(self) -> Dict[str, Any]:
        """Return runtime bot settings as key -> value from Supabase."""
        def _run():
            client = self.connect_sync()
            result = client.table("bot_settings").select("key,value").execute()
            rows = result.data or []
            return {str(row.get("key")): row.get("value") for row in rows if row.get("key")}

        return await asyncio.to_thread(_run)

    async def upsert_bot_setting(self, key: str, value: str, value_type: str, admin_telegram_id: int) -> None:
        """Create/update one runtime bot setting."""
        payload = {
            "key": key,
            "value": value,
            "value_type": value_type,
            "updated_by": admin_telegram_id,
            "updated_at": _now_iso(),
        }

        def _run() -> None:
            client = self.connect_sync()
            client.table("bot_settings").upsert(payload, on_conflict="key").execute()

        await asyncio.to_thread(_run)

    async def delete_bot_setting(self, key: str) -> None:
        """Delete one runtime setting so the bot uses its default value again."""
        def _run() -> None:
            client = self.connect_sync()
            client.table("bot_settings").delete().eq("key", key).execute()

        await asyncio.to_thread(_run)

    async def stats(self) -> Dict[str, Any]:
        def _count(table: str, status: str | None = None) -> int:
            client = self.connect_sync()
            query = client.table(table).select("id", count="exact")
            if status:
                query = query.eq("status", status)
            result = query.execute()
            return int(result.count or 0)

        def _run():
            client = self.connect_sync()
            total_users = _count("users")
            total_tasks = _count("dubbing_tasks")
            completed = _count("dubbing_tasks", "completed")
            failed = _count("dubbing_tasks", "failed")
            running = _count("dubbing_tasks", "processing")
            queued = _count("dubbing_tasks", "queued")

            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            today_result = (
                client.table("dubbing_tasks")
                .select("id", count="exact")
                .gte("created_at", today_start)
                .execute()
            )
            today_tasks = int(today_result.count or 0)

            voices = client.table("dubbing_tasks").select("voice").limit(5000).execute().data or []
            voice_counts: Dict[str, int] = {}
            for row in voices:
                voice = row.get("voice")
                if voice:
                    voice_counts[voice] = voice_counts.get(voice, 0) + 1
            most_used_voice = max(voice_counts, key=voice_counts.get) if voice_counts else "N/A"

            return {
                "total_users": total_users,
                "total_tasks": total_tasks,
                "completed": completed,
                "failed": failed,
                "running": running,
                "queued": queued,
                "today_tasks": today_tasks,
                "most_used_voice": most_used_voice,
            }

        return await asyncio.to_thread(_run)


supabase_service = SupabaseService()
