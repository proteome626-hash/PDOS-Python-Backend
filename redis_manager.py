import os
import json
import asyncio
import logging

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "")

_pub = None
_sub = None

async def get_redis():
    if not REDIS_URL:
        return None
    try:
        import redis.asyncio as aioredis
        return aioredis.from_url(REDIS_URL, decode_responses=True)
    except Exception as e:
        logger.warning("Redis not available (%s); running in-memory only", e)
        return None

_redis = None

async def ensure_redis():
    global _redis
    if _redis is None and REDIS_URL:
        _redis = await get_redis()
    return _redis

async def publish(channel: str, message: dict):
    r = await ensure_redis()
    if r is None:
        return
    try:
        await r.publish(channel, json.dumps(message))
    except Exception as e:
        logger.error("Redis publish failed: %s", e)

async def subscribe(channel: str, callback):
    r = await ensure_redis()
    if r is None:
        return None
    try:
        pubsub = r.pubsub()
        await pubsub.subscribe(channel)
        return pubsub
    except Exception as e:
        logger.error("Redis subscribe failed: %s", e)
        return None

async def close():
    global _redis
    if _redis:
        await _redis.close()
        _redis = None

async def cache_get(key: str):
    r = await ensure_redis()
    if r is None:
        return None
    try:
        return await r.get(key)
    except Exception as e:
        logger.error("Redis cache_get failed: %s", e)
        return None

async def cache_set(key: str, value: str, ttl: int = 300):
    r = await ensure_redis()
    if r is None:
        return
    try:
        await r.setex(key, ttl, value)
    except Exception as e:
        logger.error("Redis cache_set failed: %s", e)
