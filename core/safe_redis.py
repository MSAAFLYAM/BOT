"""
core/safe_redis.py — Safe Redis wrapper for pipeline modules.

All operations fail-open: never raise, never crash the pipeline.
Used by: dedup.py, circuit_breaker.py, boards.py, scheduler.py

Why a separate wrapper:
  The pipeline runs in threads via asyncio.run().
  Redis connection errors must NEVER propagate to the user.
  Each operation returns a safe default on failure.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_client = None


def get_safe_redis():
    """Return sync Redis client or None if unavailable."""
    global _client
    if _client is not None:
        return _client
    try:
        import redis as _redis
        url     = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _client = _redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=False,
        )
        # Test connection
        _client.ping()
        logger.debug(f"[safe_redis] Connected to {url[:30]}")
        return _client
    except Exception as e:
        logger.warning(f"[safe_redis] Unavailable: {e} — running without cache")
        _client = None
        return None


def safe_get(key: str, default=None):
    """GET key → value or default on any error."""
    try:
        r = get_safe_redis()
        if r is None:
            return default
        val = r.get(key)
        return val if val is not None else default
    except Exception:
        return default


def safe_set(key: str, value: str, ttl: int = 3600) -> bool:
    """SET key value EX ttl → True on success, False on error."""
    try:
        r = get_safe_redis()
        if r is None:
            return False
        r.setex(key, ttl, value)
        return True
    except Exception:
        return False


def safe_delete(key: str) -> bool:
    """DEL key → True on success."""
    try:
        r = get_safe_redis()
        if r is None:
            return False
        r.delete(key)
        return True
    except Exception:
        return False


def safe_exists(key: str) -> bool:
    """EXISTS key → True/False, False on error."""
    try:
        r = get_safe_redis()
        if r is None:
            return False
        return bool(r.exists(key))
    except Exception:
        return False


def safe_incr(key: str, ttl: int = 86400) -> int:
    """INCR + EXPIRE → new value, 0 on error."""
    try:
        r = get_safe_redis()
        if r is None:
            return 0
        val = r.incr(key)
        r.expire(key, ttl)
        return val
    except Exception:
        return 0


def safe_smembers(key: str) -> set:
    """SMEMBERS → set, empty set on error."""
    try:
        r = get_safe_redis()
        if r is None:
            return set()
        members = r.smembers(key)
        return members if members else set()
    except Exception:
        return set()


def safe_sadd(key: str, *values, ttl: int = 86400) -> bool:
    """SADD + EXPIRE → True on success."""
    try:
        r = get_safe_redis()
        if r is None:
            return False
        r.sadd(key, *values)
        r.expire(key, ttl)
        return True
    except Exception:
        return False


def safe_sismember(key: str, value: str) -> bool:
    """SISMEMBER → True/False, False on error."""
    try:
        r = get_safe_redis()
        if r is None:
            return False
        return bool(r.sismember(key, value))
    except Exception:
        return False


def safe_rpush(key: str, value: str, ttl: int = 86400) -> bool:
    """RPUSH + EXPIRE → True on success."""
    try:
        r = get_safe_redis()
        if r is None:
            return False
        r.rpush(key, value)
        r.expire(key, ttl)
        return True
    except Exception:
        return False


def safe_lrange(key: str, start: int = 0, end: int = -1) -> list:
    """LRANGE → list, empty list on error."""
    try:
        r = get_safe_redis()
        if r is None:
            return []
        result = r.lrange(key, start, end)
        return result if result else []
    except Exception:
        return []


def reset_connection():
    """Force reconnect on next use (after connection error)."""
    global _client
    _client = None
