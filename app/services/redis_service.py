"""Redis state, cache, and queue service."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from redis.asyncio import Redis

from app.config import settings


class RedisService:
    def __init__(self) -> None:
        self.redis: Optional[Redis] = None

    async def connect(self) -> Redis:
        if self.redis is None:
            self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        return self.redis

    async def ping(self) -> bool:
        redis = await self.connect()
        return bool(await redis.ping())

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None

    async def get(self, key: str) -> Optional[str]:
        redis = await self.connect()
        return await redis.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        redis = await self.connect()
        await redis.set(key, value, ex=ex)

    async def delete(self, *keys: str) -> None:
        redis = await self.connect()
        if keys:
            await redis.delete(*keys)

    async def hset(self, key: str, mapping: Dict[str, Any]) -> None:
        redis = await self.connect()
        clean = {k: "" if v is None else str(v) for k, v in mapping.items()}
        await redis.hset(key, mapping=clean)

    async def hgetall(self, key: str) -> Dict[str, str]:
        redis = await self.connect()
        return await redis.hgetall(key)

    async def hget(self, key: str, field: str) -> Optional[str]:
        redis = await self.connect()
        return await redis.hget(key, field)

    async def set_user_state(self, telegram_user_id: int, state: str) -> None:
        await self.set(f"user:{telegram_user_id}:state", state, ex=60 * 60 * 24)

    async def get_user_state(self, telegram_user_id: int) -> str:
        return await self.get(f"user:{telegram_user_id}:state") or "idle"

    async def set_user_voice(self, telegram_user_id: int, voice: str) -> None:
        await self.set(f"user:{telegram_user_id}:voice", voice, ex=60 * 60 * 24 * 30)

    async def get_user_voice(self, telegram_user_id: int) -> Optional[str]:
        return await self.get(f"user:{telegram_user_id}:voice")

    async def set_user_task(self, telegram_user_id: int, task_id: str) -> None:
        await self.set(f"user:{telegram_user_id}:task", task_id, ex=60 * 60 * 24)

    async def get_user_task(self, telegram_user_id: int) -> Optional[str]:
        return await self.get(f"user:{telegram_user_id}:task")

    async def clear_user_flow(self, telegram_user_id: int) -> None:
        await self.delete(
            f"user:{telegram_user_id}:state",
            f"user:{telegram_user_id}:task",
        )

    async def set_task_meta(self, task_id: str, mapping: Dict[str, Any]) -> None:
        await self.hset(f"task:{task_id}:meta", mapping)

    async def get_task_meta(self, task_id: str) -> Dict[str, str]:
        return await self.hgetall(f"task:{task_id}:meta")

    async def set_task_status(self, task_id: str, status: str, progress: int | None = None) -> None:
        mapping: Dict[str, Any] = {"status": status}
        if progress is not None:
            mapping["progress"] = progress
        await self.hset(f"task:{task_id}:status", mapping)

    async def get_task_status(self, task_id: str) -> Dict[str, str]:
        return await self.hgetall(f"task:{task_id}:status")

    async def enqueue(self, payload: Dict[str, Any]) -> None:
        redis = await self.connect()
        await redis.rpush(settings.redis_queue_key, json.dumps(payload, ensure_ascii=False))

    async def dequeue(self, timeout: int = 5) -> Optional[Dict[str, Any]]:
        redis = await self.connect()
        item = await redis.blpop(settings.redis_queue_key, timeout=timeout)
        if not item:
            return None
        _, raw = item
        return json.loads(raw)

    async def queue_count(self) -> int:
        redis = await self.connect()
        return int(await redis.llen(settings.redis_queue_key))


redis_service = RedisService()
