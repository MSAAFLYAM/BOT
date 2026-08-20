"""
core/health.py — FastAPI Health Check API.

Architecture decisions:
  - Standalone FastAPI app, mountable on ANY existing ASGI/WSGI app.
  - Can run alongside the existing Flask bot as a separate service,
    OR be mounted directly inside the future FastAPI gateway.
  - Each check is independent: DB down ≠ Redis down ≠ total failure.
  - Returns structured JSON with per-service status + latency.
  - /health/live  → Kubernetes-style liveness probe (is the process alive?)
  - /health/ready → Kubernetes-style readiness probe (can it serve traffic?)
  - /health       → Full detailed status (DB + Redis + config)
  - /             → Root redirect to /health

Health check configuration:
  - /health is the default health check path.
  - If /health returns non-2xx, deployment marks as failed.
  - Response time must be < 5 seconds.

Running standalone (for testing):
  uvicorn core.health:app --host 0.0.0.0 --port 8080 --reload

Running in production:
  uvicorn core.health:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import asyncio
import logging
import platform
import sys
import time
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Response Models ───────────────────────────────────────────────────────────

class ServiceCheck(BaseModel):
    """Status of a single dependent service."""
    status:     Literal["ok", "degraded", "error", "unconfigured"]
    latency_ms: Optional[float] = None
    message:    Optional[str]   = None
    details:    Optional[dict]  = None


class HealthResponse(BaseModel):
    """Full health check response."""
    status:      Literal["ok", "degraded", "error"]
    environment: str
    version:     str = "2.0.0"
    uptime_s:    float
    timestamp:   str
    services:    dict[str, ServiceCheck]
    config:      dict[str, str]


class LivenessResponse(BaseModel):
    """Minimal liveness probe response (< 5ms, no DB check)."""
    status:    Literal["alive"]
    timestamp: str
    pid:       int


class ReadinessResponse(BaseModel):
    """Readiness probe — checks if all critical services are available."""
    ready:     bool
    timestamp: str
    checks:    dict[str, bool]


# ── App startup time (for uptime calculation) ─────────────────────────────────
_START_TIME = time.monotonic()


# ── FastAPI Application ────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Affiliate SaaS — Health API",
    description=(
        "Health check endpoints for deployment monitoring.\n\n"
        "**Endpoints:**\n"
        "- `GET /` → redirect to /health\n"
        "- `GET /health/live` → liveness probe (process alive?)\n"
        "- `GET /health/ready` → readiness probe (services available?)\n"
        "- `GET /health` → full detailed health status"
    ),
    version="2.0.0",
    docs_url="/health/docs",     # Swagger UI
    redoc_url="/health/redoc",   # ReDoc UI
    openapi_url="/health/openapi.json",
)


# ── Individual Service Checks ─────────────────────────────────────────────────

async def _check_postgresql() -> ServiceCheck:
    """
    Check PostgreSQL connectivity and measure latency.
    Uses the existing async engine from core.database.
    """
    try:
        from core.database import check_database_health
        result = await asyncio.wait_for(
            check_database_health(),
            timeout=4.0,
        )
        if result["status"] == "ok":
            return ServiceCheck(
                status="ok",
                latency_ms=result.get("latency_ms"),
                message="PostgreSQL connected",
            )
        return ServiceCheck(
            status="error",
            message=result.get("error", "Unknown database error"),
        )
    except asyncio.TimeoutError:
        return ServiceCheck(
            status="error",
            message="PostgreSQL health check timed out (>4s)",
        )
    except Exception as e:
        return ServiceCheck(
            status="error",
            message=f"PostgreSQL error: {str(e)[:100]}",
        )


async def _check_redis() -> ServiceCheck:
    """
    Check Redis connectivity and measure latency.
    Uses the existing Redis pool from core.redis_client.
    """
    try:
        from core.redis_client import check_redis_health
        result = await asyncio.wait_for(
            check_redis_health(),
            timeout=3.0,
        )
        if result["status"] == "ok":
            return ServiceCheck(
                status="ok",
                latency_ms=result.get("latency_ms"),
                message="Redis connected",
            )
        return ServiceCheck(
            status="error",
            message=result.get("error", "Unknown Redis error"),
        )
    except asyncio.TimeoutError:
        return ServiceCheck(
            status="error",
            message="Redis health check timed out (>3s)",
        )
    except Exception as e:
        return ServiceCheck(
            status="error",
            message=f"Redis error: {str(e)[:100]}",
        )


async def _check_telegram() -> ServiceCheck:
    """
    Validate Telegram Bot token format (no API call — just config check).
    Full connectivity test would use bot.get_me() but that's overkill for health.
    """
    try:
        from core.config import get_settings
        settings = get_settings()
        token = settings.telegram.BOT_TOKEN
        if not token:
            return ServiceCheck(
                status="unconfigured",
                message="BOT_TOKEN not set",
            )
        # Validate format: must be "digits:alphanum"
        import re
        if not re.match(r"^\d+:[A-Za-z0-9_-]{35,}$", token):
            return ServiceCheck(
                status="error",
                message="BOT_TOKEN format invalid",
            )
        return ServiceCheck(
            status="ok",
            message="Telegram token configured",
            details={"bot_id": token.split(":")[0]},
        )
    except Exception as e:
        return ServiceCheck(
            status="error",
            message=f"Telegram config error: {str(e)[:100]}",
        )


async def _check_openrouter() -> ServiceCheck:
    """Check if OpenRouter API key is configured (no API call)."""
    try:
        from core.config import get_settings
        settings = get_settings()
        if not settings.ai.OPENROUTER_API_KEY:
            return ServiceCheck(
                status="unconfigured",
                message="OPENROUTER_API_KEY not set — AI generation disabled",
            )
        return ServiceCheck(
            status="ok",
            message=f"AI configured: {settings.ai.OPENROUTER_MODEL}",
        )
    except Exception as e:
        return ServiceCheck(status="error", message=str(e)[:100])


async def _check_affiliate() -> ServiceCheck:
    """Check if Amazon affiliate tag is configured."""
    try:
        from core.config import get_settings
        settings = get_settings()
        tag = settings.amazon.AFFILIATE_TAG
        if not tag:
            return ServiceCheck(
                status="degraded",
                message="AFFILIATE_TAG not set — links have no affiliate tag!",
            )
        return ServiceCheck(
            status="ok",
            message=f"Affiliate tag: {tag}",
        )
    except Exception as e:
        return ServiceCheck(status="error", message=str(e)[:100])


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get(
    "/",
    include_in_schema=False,
    summary="Root — redirects to /health",
)
async def root(response: Response):
    """Redirect root to /health for convenience."""
    response.headers["Location"] = "/health"
    response.status_code = 307
    return {"redirect": "/health"}


@app.get(
    "/health/live",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description=(
        "Ultra-fast liveness check. No DB/Redis calls. "
        "Returns 200 if the Python process is alive. "
        "Used by orchestrators to detect crashed processes."
    ),
    tags=["Health"],
)
async def liveness() -> LivenessResponse:
    """
    Liveness probe — is the process alive?

    Must respond in < 100ms.
    If this fails, orchestrator will restart the container.
    """
    return LivenessResponse(
        status="alive",
        timestamp=datetime.now(timezone.utc).isoformat(),
        pid=__import__("os").getpid(),
    )


@app.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Readiness check — can the service handle traffic? "
        "Checks PostgreSQL and Redis. "
        "Returns 200 if ready, 503 if not."
    ),
    tags=["Health"],
)
async def readiness(response: Response) -> ReadinessResponse:
    """
    Readiness probe — can the service serve requests?

    Runs DB and Redis checks in parallel for speed.
    Returns 503 if any critical service is unavailable.
    Orchestrator uses this to decide whether to route traffic.
    """
    # Run critical checks in parallel
    db_check, redis_check = await asyncio.gather(
        _check_postgresql(),
        _check_redis(),
        return_exceptions=True,
    )

    db_ok    = isinstance(db_check, ServiceCheck) and db_check.status == "ok"
    redis_ok = isinstance(redis_check, ServiceCheck) and redis_check.status == "ok"
    is_ready = db_ok and redis_ok

    if not is_ready:
        response.status_code = 503  # Service Unavailable

    return ReadinessResponse(
        ready=is_ready,
        timestamp=datetime.now(timezone.utc).isoformat(),
        checks={
            "postgresql": db_ok,
            "redis":      redis_ok,
        },
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Full health status",
    description=(
        "Complete health check with all service statuses and latencies. "
        "Returns 200 if all critical services are OK, "
        "200 with status='degraded' if non-critical services are down, "
        "503 if critical services (DB/Redis) are down."
    ),
    tags=["Health"],
)
async def health(response: Response) -> HealthResponse:
    """
    Full health check — all services, all latencies.

    Runs all checks in parallel for minimum response time.
    Critical services: PostgreSQL, Redis.
    Non-critical (degraded): Telegram, OpenRouter, Affiliate tag.
    """
    # Run ALL checks in parallel
    start = time.monotonic()
    results = await asyncio.gather(
        _check_postgresql(),
        _check_redis(),
        _check_telegram(),
        _check_openrouter(),
        _check_affiliate(),
        return_exceptions=True,
    )
    total_ms = (time.monotonic() - start) * 1000

    # Unpack (handle exceptions from gather)
    def _safe(result, name: str) -> ServiceCheck:
        if isinstance(result, ServiceCheck):
            return result
        if isinstance(result, Exception):
            return ServiceCheck(status="error", message=f"Check crashed: {result}")
        return ServiceCheck(status="error", message="Unknown check result")

    db_check       = _safe(results[0], "postgresql")
    redis_check    = _safe(results[1], "redis")
    telegram_check = _safe(results[2], "telegram")
    ai_check       = _safe(results[3], "openrouter")
    affiliate_check= _safe(results[4], "affiliate")

    # Determine overall status
    critical_ok = (
        db_check.status == "ok"
        and redis_check.status == "ok"
    )
    any_error = any(
        c.status == "error"
        for c in [db_check, redis_check, telegram_check, ai_check, affiliate_check]
    )
    any_degraded = any(
        c.status in ("degraded", "unconfigured")
        for c in [telegram_check, ai_check, affiliate_check]
    )

    if not critical_ok:
        overall = "error"
        response.status_code = 503
    elif any_error or any_degraded:
        overall = "degraded"
        # Still 200 — degraded means non-critical services have issues
    else:
        overall = "ok"

    # Config summary (no secrets — only presence indicators)
    from core.config import get_settings
    settings = get_settings()
    config_summary = {
        "environment":    settings.ENVIRONMENT,
        "db_pool_size":   str(settings.database.DB_POOL_SIZE),
        "redis_max_conn": str(settings.redis.REDIS_MAX_CONNECTIONS),
        "affiliate_tag":  settings.amazon.AFFILIATE_TAG or "❌ not set",
        "channel_id":     settings.telegram.CHANNEL_ID or "❌ not set",
        "wp_configured":  "✅" if settings.wordpress.is_configured else "❌",
        "blogger_config": "✅" if settings.blogger.is_configured else "❌",
        "wa_configured":  "✅" if settings.whatsapp.is_configured else "❌",
        "sheets_config":  "✅" if settings.sheets.is_configured else "❌",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform":       platform.system(),
    }

    return HealthResponse(
        status=overall,
        environment=settings.ENVIRONMENT,
        uptime_s=round(time.monotonic() - _START_TIME, 1),
        timestamp=datetime.now(timezone.utc).isoformat(),
        services={
            "postgresql": db_check,
            "redis":      redis_check,
            "telegram":   telegram_check,
            "openrouter": ai_check,
            "affiliate":  affiliate_check,
        },
        config=config_summary,
    )


# ── Startup / Shutdown Events ──────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    """
    Runs when the FastAPI app starts.
    Log startup info and verify critical services.
    """
    logger.info(
        "Health API starting",
        extra={
            "pid":     __import__("os").getpid(),
            "python":  sys.version,
            "platform": platform.system(),
        },
    )
    # Non-blocking: log initial health status
    try:
        from core.database import check_database_health
        from core.redis_client import check_redis_health
        db_status    = await check_database_health()
        redis_status = await check_redis_health()
        logger.info(f"PostgreSQL: {db_status}")
        logger.info(f"Redis: {redis_status}")
    except Exception as e:
        logger.warning(f"Startup health check failed: {e}")


@app.on_event("shutdown")
async def on_shutdown():
    """Graceful shutdown — close connection pools."""
    logger.info("Health API shutting down...")
    try:
        from core.database import close_engine
        from core.redis_client import close_redis
        await close_engine()
        await close_redis()
        logger.info("All connections closed cleanly.")
    except Exception as e:
        logger.warning(f"Shutdown cleanup error: {e}")


# ── Standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(
        "core.health:app",
        host="0.0.0.0",
        port=port,
        reload=False,           # No reload in production
        log_level="info",
        access_log=True,
        # Graceful timeout
        timeout_graceful_shutdown=10,
    )
