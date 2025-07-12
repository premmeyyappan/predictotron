"""
Redis client wrapper providing typed helpers for caching and pub/sub.

The client is designed as a thin layer over redis-py's async interface,
adding serialisation, TTL defaults, and a structured pub/sub context manager.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import redis.asyncio as aioredis

from app.config import settings


_redis_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
    return _redis_pool


async def close_redis() -> None:
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None


async def cache_set(key: str, value: Any, ttl: int = 60) -> None:
    r = await get_redis()
    await r.set(key, json.dumps(value), ex=ttl)


async def cache_get(key: str) -> Any | None:
    r = await get_redis()
    raw = await r.get(key)
    return json.loads(raw) if raw is not None else None


async def cache_delete(key: str) -> None:
    r = await get_redis()
    await r.delete(key)


async def publish(channel: str, message: dict[str, Any]) -> None:
    r = await get_redis()
    await r.publish(channel, json.dumps(message))


@asynccontextmanager
async def subscribe(channel: str) -> AsyncGenerator[aioredis.client.PubSub, None]:
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)
    try:
        yield pubsub
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
