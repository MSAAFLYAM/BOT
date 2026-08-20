"""
api/dependencies.py — FastAPI dependency injection.

Provides reusable dependencies:
  - get_db()      → async DB session (auto-closed after request)
  - get_redis()   → Redis client
  - verify_token()→ API key authentication
  - rate_limit()  → per-IP rate limiting
  - get_settings()→ app settings

Architecture decisions:
  - API key auth via X-API-Key header OR ?api_key= query param.
  - Rate limiting: 60 req/min per IP for public endpoints,
    unlimited for authenticated endpoints.
  - DB session auto-committed and closed after each request
    (FastAPI's Depends() handles cleanup via yield).
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import AsyncGenerator, Optional

from fastapi import Depends, Header, HTTPException, Query, Request, status

logger = logging.getLogger(__name__)


# ── Database session ──────────────────────────────────────────────────────────

async def get_db():
    """
    Provide an async DB session for the duration of the request.
    Auto-commits on success, rolls back on exception.
    """
    from core.database import get_db as _get_db
    async with _get_db() as session:
        yield session


# ── Redis client ──────────────────────────────────────────────────────────────

def get_redis():
    """Provide Redis client."""
    from core.redis_client import get_redis as _get_redis
    return _get_redis()


# ── Settings ──────────────────────────────────────────────────────────────────

def get_settings():
    """Provide app settings."""
    from core.config import get_settings as _get_settings
    return _get_settings()


# ── API Key Authentication ────────────────────────────────────────────────────

async def verify_token(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key:   Optional[str] = Query(None),
    settings                 = Depends(get_settings),
) -> bool:
    """
    Verify API key from header or query param.

    Usage in route:
        @router.get("/admin/jobs")
        async def list_jobs(auth: bool = Depends(verify_token)):
            ...

    API key must match ADMIN_API_KEY env variable.
    If ADMIN_API_KEY is not set, admin endpoints are disabled.
    """
    token = x_api_key or api_key
    admin_key = getattr(settings, "ADMIN_API_KEY", None) or ""

    if not admin_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API not configured (ADMIN_API_KEY not set)",
        )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required",
        )

    # Constant-time comparison to prevent timing attacks
    is_valid = hashlib.sha256(token.encode()).digest() == \
               hashlib.sha256(admin_key.encode()).digest()

    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return True


# ── Rate Limiting ─────────────────────────────────────────────────────────────

async def rate_limit(
    request:  Request,
    rpm:      int = 60,
) -> None:
    """
    Per-IP rate limiting using Redis.
    Default: 60 requests/minute per IP.

    Usage:
        @router.get("/search")
        async def search(rl = Depends(rate_limit)):
            ...
    """
    try:
        from core.redis_client import get_redis as _get_redis
        redis = _get_redis()
        ip    = request.client.host if request.client else "unknown"
        key   = f"ratelimit:api:{ip}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count > rpm:
            retry_after = await redis.ttl(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Retry after {retry_after}s",
                headers={"Retry-After": str(retry_after)},
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Fail open if Redis unavailable


def rate_limit_strict(rpm: int = 20):
    """
    Factory for stricter rate limits on sensitive endpoints.

    Usage:
        @router.post("/scrape")
        async def scrape(rl = Depends(rate_limit_strict(10))):
            ...
    """
    async def _limit(request: Request):
        await rate_limit(request, rpm=rpm)
    return _limit
