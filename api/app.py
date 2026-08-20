"""
api/app.py — FastAPI main application (Phase 6).

Fix: startup is completely non-blocking.
     /health/live responds instantly (no DB calls).
"""
from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Amazon Affiliate Bot v4 — SaaS API",
        version="4.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Middleware ────────────────────────────────────────────────
    try:
        from api.middleware import setup_middleware
        setup_middleware(app)
    except Exception as e:
        logger.warning(f"Middleware setup failed: {e}")

    # ── Routes ────────────────────────────────────────────────────
    try:
        from api.routes import (
            health_router, products_router,
            admin_router,
        )
        app.include_router(health_router)
        app.include_router(products_router)
        app.include_router(admin_router)
    except Exception as e:
        logger.warning(f"Routes setup failed: {e}")

    try:
        from api.webhook import router as webhook_router
        app.include_router(webhook_router)
    except Exception as e:
        logger.warning(f"Webhook router failed: {e}")

    # ── CRITICAL: /health/live must always work ───────────────────
    @app.get("/health/live", tags=["Health"])
    async def liveness():
        return {"status": "alive", "pid": os.getpid()}

    @app.get("/health", tags=["Health"])
    async def health():
        return {
            "status":  "ok",
            "service": "Amazon Affiliate Bot v4",
            "version": "4.0.0",
        }

    @app.get("/", include_in_schema=False)
    async def root():
        return JSONResponse(
            {"status": "ok", "bot": "Amazon Affiliate Bot v4"},
            status_code=200,
        )

    # ── Lifecycle ─────────────────────────────────────────────────
    @app.on_event("startup")
    async def on_startup():
        logger.info("FastAPI starting...")
        # All startup tasks are fire-and-forget (non-blocking)
        asyncio.create_task(_background_startup())

    @app.on_event("shutdown")
    async def on_shutdown():
        logger.info("FastAPI shutting down...")
        try:
            from api.webhook import stop_webhook_consumer
            await stop_webhook_consumer()
        except Exception:
            pass

    return app


async def _background_startup():
    """
    Non-blocking background initialization.
    Runs AFTER the server is already accepting requests.
    This ensures /health/live responds immediately.
    """
    await asyncio.sleep(2)  # Let server fully start first

    # DB pool
    try:
        from core.database import engine
        logger.info("DB engine ready")
    except Exception as e:
        logger.warning(f"DB init: {e}")

    # Webhook consumer
    try:
        from api.webhook import start_webhook_consumer
        await start_webhook_consumer()
        logger.info("Webhook consumer started")
    except Exception as e:
        logger.warning(f"Webhook consumer: {e}")

    # Register webhook with Telegram
    try:
        await _register_webhook()
    except Exception as e:
        logger.warning(f"Webhook registration: {e}")

    logger.info("✅ Background startup complete")


async def _register_webhook():
    token  = os.environ.get("BOT_TOKEN", "")
    domain = os.environ.get("PUBLIC_DOMAIN", "")
    secret = os.environ.get("WEBHOOK_SECRET", "")

    if not token or not domain:
        return

    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        url = f"https://{domain}/webhook/{secret}"
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": url, "allowed_updates": ["message", "callback_query"]},
        )
        if resp.json().get("ok"):
            logger.info(f"Webhook registered: {url}")


app = create_app()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("api.app:app", host="0.0.0.0", port=port)
