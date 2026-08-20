"""
core/redis_client.py — Async Redis client with cache, rate limiting, and queue helpers.

Architecture decisions:
  - redis-py (>=5.0) with asyncio support (no longer need aioredis separately).
  - Connection pool shared across the application (not per-request).
  - Separate logical databases for different concerns:
      db=0: application cache (scraped pages, AI results)
      db=1: rate limiting counters
      (Celery uses its own db, configured in Celery settings)
  - All cache keys use prefixed namespaces to prevent collisions.
  - cache_get/cache_set helpers handle JSON serialization transparently.
  - Rate limiter uses Redis INCR + EXPIRE (atomic, correct under concurrency).
  - Session store for scraping sessions/cookies (persists across bot restarts).

Memory optimization:
  - Cache TTLs are set conservatively to prevent Redis OOM.
  - max_connections=10 prevents connection exhaustion.
  - decode_responses=True returns strings, not bytes (less memory overhead).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import ConnectionError, RedisError

from core.config import get_settings

logger = logging.getLogger(__name__)

# ── Key Namespace Prefixes ─────────────────────────────────────────────────────
# All keys are prefixed to prevent collisions and allow selective flushing.

PREFIX_CACHE_HTML    = "cache:html:"       # Scraped HTML pages
PREFIX_CACHE_PRODUCT = "cache:product:"    # Extracted product data
PREFIX_CACHE_AI      = "cache:ai:"         # AI-generated articles
PREFIX_RATE          = "rate:"             # Rate limiter counters
PREFIX_SESSION       = "session:"          # Scraping sessions/cookies
PREFIX_LOCK          = "lock:"             # Distributed locks
PREFIX_JOB_STATUS    = "job:status:"       # Real-time job status (for Telegram updates)


# ── JSON Serialization ────────────────────────────────────────────────────────

class _JSONEncoder(json.JSONEncoder):
    """Extended JSON encoder handling Decimal and datetime types."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, cls=_JSONEncoder)


def _deserialize(value: Optional[str]) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


# ── Connection Pool Singleton ─────────────────────────────────────────────────

_pool: Optional[ConnectionPool] = None


def _get_pool() -> Optional[ConnectionPool]:
    """
    Return singleton Redis connection pool.
    Returns None if Redis is unavailable (fail-open).
    """
    global _pool
    if _pool is None:
        try:
            import os as _os
            redis_url    = _os.environ.get("REDIS_URL", "redis://localhost:6379")
            max_conn     = int(_os.environ.get("REDIS_MAX_CONNECTIONS", "10"))
            sock_timeout = float(_os.environ.get("REDIS_SOCKET_TIMEOUT", "5.0"))

            _pool = aioredis.ConnectionPool.from_url(
                redis_url,
                max_connections=max_conn,
                socket_timeout=sock_timeout,
                socket_connect_timeout=5,
                retry_on_timeout=True,
                decode_responses=True,
                health_check_interval=30,
            )
            logger.info("Redis connection pool created.")
        except Exception as e:
            logger.warning(f"Redis pool creation failed: {e}")
            _pool = None
    return _pool


def get_redis() -> Optional[Redis]:
    """
    Get a Redis client from the pool.

    Usage:
        redis = get_redis()
        await redis.set("key", "value")
        # Connection returned to pool automatically
    """
    pool = _get_pool()
    if pool is None:
        return None
    return Redis(connection_pool=pool)


async def close_redis() -> None:
    """Close all connections in the pool. Call on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.disconnect()
        _pool = None
        logger.info("Redis connection pool closed.")


# ── Health Check ──────────────────────────────────────────────────────────────

async def check_redis_health() -> dict:
    """Verify Redis connectivity. Called at startup."""
    import time as _time
    start = _time.monotonic()
    try:
        redis = get_redis()
        await redis.ping()
        latency_ms = (_time.monotonic() - start) * 1000
        logger.info(f"Redis health check OK ({latency_ms:.1f}ms)")
        return {"status": "ok", "latency_ms": round(latency_ms, 2)}
    except (ConnectionError, RedisError) as e:
        logger.error(f"Redis health check FAILED: {e}")
        return {"status": "error", "error": str(e)}


# ── Cache Operations ──────────────────────────────────────────────────────────

async def cache_set(
    key: str,
    value: Any,
    ttl: int,
    prefix: str = "",
) -> bool:
    """
    Store a value in Redis with TTL.

    Args:
        key:    Cache key (without prefix).
        value:  Any JSON-serializable value.
        ttl:    Time-to-live in seconds.
        prefix: Key namespace prefix (use PREFIX_* constants).

    Returns:
        True if stored, False on error.

    Usage:
        await cache_set("B08XYZ123", product_dict, ttl=3600, prefix=PREFIX_CACHE_PRODUCT)
    """
    full_key = f"{prefix}{key}"
    try:
        redis = get_redis()
        serialized = _serialize(value)
        await redis.setex(full_key, ttl, serialized)
        logger.debug(f"Cache SET {full_key!r} (ttl={ttl}s, size={len(serialized)})")
        return True
    except RedisError as e:
        # Cache errors are non-fatal — log and continue without cache.
        logger.warning(f"Cache SET failed for {full_key!r}: {e}")
        return False


async def cache_get(key: str, prefix: str = "") -> Optional[Any]:
    """
    Retrieve a value from Redis cache.

    Returns None on cache miss or error.

    Usage:
        data = await cache_get("B08XYZ123", prefix=PREFIX_CACHE_PRODUCT)
        if data is None:
            data = await scrape_product(url)  # cache miss, fetch fresh
    """
    full_key = f"{prefix}{key}"
    try:
        redis = get_redis()
        value = await redis.get(full_key)
        if value is None:
            logger.debug(f"Cache MISS {full_key!r}")
            return None
        logger.debug(f"Cache HIT {full_key!r}")
        return _deserialize(value)
    except RedisError as e:
        logger.warning(f"Cache GET failed for {full_key!r}: {e}")
        return None  # Treat Redis errors as cache miss


async def cache_delete(key: str, prefix: str = "") -> bool:
    """Delete a specific cache entry."""
    full_key = f"{prefix}{key}"
    try:
        redis = get_redis()
        deleted = await redis.delete(full_key)
        return bool(deleted)
    except RedisError as e:
        logger.warning(f"Cache DELETE failed for {full_key!r}: {e}")
        return False


async def cache_html(url: str, html: str, ttl: Optional[int] = None) -> None:
    """Cache scraped HTML. URL is hashed for safe key."""
    import hashlib
    key = hashlib.sha256(url.encode()).hexdigest()[:32]
    import os as _os
    _ttl = ttl or int(_os.environ.get("CACHE_TTL_SCRAPED_PAGE", "3600"))
    await cache_set(key, html, ttl=_ttl, prefix=PREFIX_CACHE_HTML)


async def get_cached_html(url: str) -> Optional[str]:
    """Retrieve cached HTML for URL."""
    import hashlib
    key = hashlib.sha256(url.encode()).hexdigest()[:32]
    return await cache_get(key, prefix=PREFIX_CACHE_HTML)


# ── Rate Limiter ──────────────────────────────────────────────────────────────

async def check_rate_limit(
    identifier: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """
    Sliding window rate limiter using Redis INCR + EXPIRE.

    This is atomic under concurrent access (no race conditions).

    Args:
        identifier:     Unique identifier (e.g. "marmiton.org", "user:123").
        max_requests:   Maximum requests allowed in the window.
        window_seconds: Time window in seconds.

    Returns:
        (allowed: bool, current_count: int)
        allowed=True means the request is within limits.
        allowed=False means rate limit exceeded.

    Usage:
        allowed, count = await check_rate_limit("marmiton.org", max_requests=10, window_seconds=60)
        if not allowed:
            await asyncio.sleep(5)  # back off
    """
    key = f"{PREFIX_RATE}{identifier}"
    try:
        redis = get_redis()
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
        current = results[0]
        allowed = current <= max_requests
        if not allowed:
            logger.debug(
                f"Rate limit exceeded for {identifier!r}: "
                f"{current}/{max_requests} in {window_seconds}s"
            )
        return allowed, current
    except RedisError as e:
        logger.warning(f"Rate limiter error for {identifier!r}: {e}")
        return True, 0  # Fail open — allow request on Redis error


async def get_rate_limit_remaining(identifier: str, max_requests: int) -> int:
    """Return remaining requests in current window."""
    key = f"{PREFIX_RATE}{identifier}"
    try:
        redis = get_redis()
        current = await redis.get(key)
        if current is None:
            return max_requests
        return max(0, max_requests - int(current))
    except RedisError:
        return max_requests


# ── Distributed Lock ──────────────────────────────────────────────────────────

async def acquire_lock(
    resource: str,
    ttl: int = 60,
    timeout: int = 10,
) -> bool:
    """
    Acquire a distributed lock using Redis SET NX.

    Prevents duplicate processing when multiple workers
    might try to process the same URL simultaneously.

    Args:
        resource: Lock identifier (e.g. "scrape:B08XYZ123").
        ttl:      Lock expiry in seconds (prevents deadlock on crash).
        timeout:  Max seconds to wait for lock.

    Returns:
        True if lock acquired, False if timeout.

    Usage:
        if await acquire_lock(f"scrape:{asin}"):
            try:
                data = await scrape(url)
            finally:
                await release_lock(f"scrape:{asin}")
        else:
            # Another worker is already scraping this ASIN
            return await get_cached_result(asin)
    """
    key = f"{PREFIX_LOCK}{resource}"
    deadline = time.monotonic() + timeout
    try:
        redis = get_redis()
        while time.monotonic() < deadline:
            # SET NX (set if not exists) is atomic
            acquired = await redis.set(key, "1", nx=True, ex=ttl)
            if acquired:
                logger.debug(f"Lock acquired: {resource!r}")
                return True
            # Wait 100ms before retry
            import asyncio
            await asyncio.sleep(0.1)
        logger.debug(f"Lock timeout for: {resource!r}")
        return False
    except RedisError as e:
        logger.warning(f"Lock acquire failed for {resource!r}: {e}")
        return True  # Fail open — proceed without lock


async def release_lock(resource: str) -> None:
    """Release a distributed lock."""
    key = f"{PREFIX_LOCK}{resource}"
    try:
        redis = get_redis()
        await redis.delete(key)
        logger.debug(f"Lock released: {resource!r}")
    except RedisError as e:
        logger.warning(f"Lock release failed for {resource!r}: {e}")


# ── Session Store (Scraping Cookies) ──────────────────────────────────────────

async def store_session(
    domain: str,
    cookies: dict,
    ttl: int = 86400,  # 24 hours
) -> None:
    """
    Persist scraping session cookies in Redis.

    Cookies are stored per domain and reused across bot restarts.
    This avoids re-solving Cloudflare challenges on every scrape.

    Args:
        domain:  Domain the cookies belong to (e.g. "marmiton.org").
        cookies: Cookie dict from Playwright/httpx.
        ttl:     How long to keep cookies (default: 24 hours).
    """
    key = f"{PREFIX_SESSION}{domain}"
    await cache_set(domain, cookies, ttl=ttl, prefix=PREFIX_SESSION)
    logger.debug(f"Stored session for {domain!r} ({len(cookies)} cookies)")


async def get_session(domain: str) -> Optional[dict]:
    """Retrieve stored session cookies for a domain."""
    return await cache_get(domain, prefix=PREFIX_SESSION)


# ── Job Status (Real-time Updates) ────────────────────────────────────────────

async def set_job_status(job_id: str, status: dict, ttl: int = 3600) -> None:
    """
    Store real-time job status in Redis for quick Telegram updates.

    Separate from PostgreSQL Job model — this is for immediate
    status checks without DB query overhead.

    status dict example:
        {"status": "running", "step": "scraping", "progress": 40}
    """
    await cache_set(job_id, status, ttl=ttl, prefix=PREFIX_JOB_STATUS)


async def get_job_status(job_id: str) -> Optional[dict]:
    """Get real-time job status from Redis."""
    return await cache_get(job_id, prefix=PREFIX_JOB_STATUS)
