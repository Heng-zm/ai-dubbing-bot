"""Redis state, cache, and queue service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings


class RedisService:
    def __init__(self) -> None:
        self.redis: Optional[Redis] = None

    async def connect(self) -> Redis:
        if self.redis is None:
            self.redis = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=settings.redis_socket_timeout_seconds,
                socket_connect_timeout=settings.redis_socket_timeout_seconds,
                health_check_interval=30,
                retry_on_timeout=True,
            )
        return self.redis

    async def ping(self) -> bool:
        try:
            redis = await self.connect()
            return bool(await redis.ping())
        except RedisError:
            return False

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None

    async def get(self, key: str) -> Optional[str]:
        redis = await self.connect()
        return await redis.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        redis = await self.connect()
        await redis.set(key, str(value), ex=ex)

    async def set_json(self, key: str, value: Dict[str, Any], ex: int | None = None) -> None:
        await self.set(key, json.dumps(value, ensure_ascii=False), ex=ex)

    async def get_json(self, key: str) -> Dict[str, Any]:
        raw = await self.get(key)
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    async def delete(self, *keys: str) -> None:
        redis = await self.connect()
        if keys:
            await redis.delete(*keys)

    async def hset(self, key: str, mapping: Dict[str, Any], ex: int | None = None) -> None:
        redis = await self.connect()
        clean = {k: "" if v is None else str(v) for k, v in mapping.items()}
        pipe = redis.pipeline()
        pipe.hset(key, mapping=clean)
        if ex:
            pipe.expire(key, ex)
        await pipe.execute()

    async def hgetall(self, key: str) -> Dict[str, str]:
        redis = await self.connect()
        return await redis.hgetall(key)

    async def hget(self, key: str, field: str) -> Optional[str]:
        redis = await self.connect()
        return await redis.hget(key, field)

    async def set_user_state(self, telegram_user_id: int, state: str) -> None:
        await self.set(f"user:{telegram_user_id}:state", state, ex=settings.task_ttl_seconds)

    async def get_user_state(self, telegram_user_id: int) -> str:
        return await self.get(f"user:{telegram_user_id}:state") or "idle"

    async def set_user_voice(self, telegram_user_id: int, voice: str) -> None:
        await self.set(f"user:{telegram_user_id}:voice", voice, ex=60 * 60 * 24 * 30)

    async def get_user_voice(self, telegram_user_id: int) -> Optional[str]:
        return await self.get(f"user:{telegram_user_id}:voice")

    async def set_user_task(self, telegram_user_id: int, task_id: str) -> None:
        await self.set(f"user:{telegram_user_id}:task", task_id, ex=settings.task_ttl_seconds)

    async def get_user_task(self, telegram_user_id: int) -> Optional[str]:
        return await self.get(f"user:{telegram_user_id}:task")

    async def clear_user_flow(self, telegram_user_id: int) -> None:
        await self.delete(
            f"user:{telegram_user_id}:state",
            f"user:{telegram_user_id}:task",
        )

    async def set_task_meta(self, task_id: str, mapping: Dict[str, Any]) -> None:
        await self.hset(f"task:{task_id}:meta", mapping, ex=settings.task_ttl_seconds)

    async def get_task_meta(self, task_id: str) -> Dict[str, str]:
        return await self.hgetall(f"task:{task_id}:meta")

    async def set_task_status(self, task_id: str, status: str, progress: int | None = None) -> None:
        mapping: Dict[str, Any] = {"status": status}
        if progress is not None:
            mapping["progress"] = max(0, min(100, int(progress)))
        await self.hset(f"task:{task_id}:status", mapping, ex=settings.task_ttl_seconds)

    async def get_task_status(self, task_id: str) -> Dict[str, str]:
        return await self.hgetall(f"task:{task_id}:status")

    async def acquire_task_lock(self, task_id: str, owner: str) -> bool:
        redis = await self.connect()
        return bool(await redis.set(f"task:{task_id}:lock", owner, nx=True, ex=settings.task_lock_ttl_seconds))

    async def refresh_task_lock(self, task_id: str) -> None:
        redis = await self.connect()
        await redis.expire(f"task:{task_id}:lock", settings.task_lock_ttl_seconds)

    async def release_task_lock(self, task_id: str, owner: str) -> None:
        redis = await self.connect()
        key = f"task:{task_id}:lock"
        current = await redis.get(key)
        if current == owner:
            await redis.delete(key)

    async def acquire_enqueue_lock(self, task_id: str, owner: str, ttl_seconds: int = 30) -> bool:
        """Acquire a short lock around queue confirmation/retry.

        Telegram users can tap inline buttons more than once, and PTB can process
        callback updates concurrently. This lock makes the status re-check and
        enqueue operation effectively single-flight for one task.
        """
        redis = await self.connect()
        return bool(await redis.set(f"task:{task_id}:enqueue_lock", owner, nx=True, ex=max(5, ttl_seconds)))

    async def release_enqueue_lock(self, task_id: str, owner: str) -> None:
        redis = await self.connect()
        key = f"task:{task_id}:enqueue_lock"
        current = await redis.get(key)
        if current == owner:
            await redis.delete(key)

    async def _queue_key(self) -> str:
        try:
            from app.services.runtime_settings import runtime_settings

            return await runtime_settings.get_str("redis_queue_key")
        except Exception:
            return settings.redis_queue_key

    async def enqueue(self, payload: Dict[str, Any]) -> int:
        """Push a job into the queue and return its 1-based position after enqueue.

        Duplicate Telegram callback taps can happen. Before pushing, scan the
        pending list for the same task_id and return the existing queue position
        instead of enqueuing a duplicate job.
        """
        redis = await self.connect()
        queue_key = await self._queue_key()
        task_id = str(payload.get("task_id") or "")
        if task_id:
            existing_position = await self.queue_position(task_id)
            if existing_position:
                return existing_position

        enriched = dict(payload)
        enriched.setdefault("enqueued_at", datetime.now(timezone.utc).isoformat())
        await redis.rpush(queue_key, json.dumps(enriched, ensure_ascii=False))
        return int(await redis.llen(queue_key))

    async def queue_position(self, task_id: str) -> Optional[int]:
        """Return the current 1-based pending queue position for a task.

        Returns None when the task is already being processed, completed, failed,
        cancelled, or not present in the pending Redis list.
        """
        redis = await self.connect()
        try:
            items = await redis.lrange(await self._queue_key(), 0, -1)
        except RedisError:
            return None
        for index, raw in enumerate(items, start=1):
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and str(payload.get("task_id")) == str(task_id):
                return index
        return None


    async def remove_task_from_queue(self, task_id: str) -> int:
        """Remove all pending queue entries for one task and return removed count.

        This prevents cancelled tasks or duplicate button taps from staying in the
        pending list. It scans the Redis list conservatively because the queue is
        intentionally small in single-service Render mode.
        """
        redis = await self.connect()
        queue_key = await self._queue_key()
        try:
            items = await redis.lrange(queue_key, 0, -1)
        except RedisError:
            return 0
        removed = 0
        for raw in items:
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and str(payload.get("task_id")) == str(task_id):
                removed += int(await redis.lrem(queue_key, 0, raw))
        return removed

    async def is_terminal_task_status(self, task_id: str) -> bool:
        status = (await self.get_task_status(task_id)).get("status")
        return status in {"completed", "failed", "cancelled"}

    async def purge_queue(self) -> int:
        """Delete all pending jobs from the Redis queue and return the previous queue size.

        On Render single-service deployments, queued jobs can point to local temp files
        that disappear after redeploy/restart. Clearing pending jobs on startup prevents
        stale jobs from failing repeatedly.
        """
        redis = await self.connect()
        queue_key = await self._queue_key()
        count = int(await redis.llen(queue_key))
        if count:
            await redis.delete(queue_key)
        return count

    async def dequeue(self, timeout: int | None = None) -> Optional[Dict[str, Any]]:
        redis = await self.connect()
        item = await redis.blpop(await self._queue_key(), timeout=timeout or settings.worker_queue_timeout_seconds)
        if not item:
            return None
        _, raw = item
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    async def requeue_later(self, payload: Dict[str, Any], delay_seconds: int = 3) -> None:
        # Simple safe requeue for transient lock conflicts. The worker sleeps before pushing back.
        import asyncio

        await asyncio.sleep(max(0, delay_seconds))
        await self.enqueue(payload)

    async def queue_count(self) -> int:
        redis = await self.connect()
        return int(await redis.llen(await self._queue_key()))


redis_service = RedisService()
