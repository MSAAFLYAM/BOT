"""
pinterest/tasks.py — Celery tasks for Pinterest automation.

Tasks:
  pinterest.pin_product  → pin Amazon product
  pinterest.pin_article  → pin article
  pinterest.daily_stats  → get daily pin statistics
  pinterest.check_cap    → check if daily cap reached

Retry strategy:
  PinterestError (rate limit 429) → retry after Retry-After header
  PinterestError (auth 401)       → NO retry (token invalid)
  Daily cap reached               → NO retry (wait until tomorrow)
  Network timeout                 → retry after 30s (max 2x)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _run(coro):
    """Run async coroutine from sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def register_tasks(celery_app):
    """Register all Pinterest Celery tasks."""

    from pinterest.client import PinterestError

    @celery_app.task(
        name="pinterest.pin_product",
        bind=True,
        max_retries=2,
        acks_late=True,
        queue="low_priority",
    )
    def pin_product_task(
        self,
        product_id:    str,
        affiliate_url: str          = "",
        chat_id:       Optional[int] = None,
        force:         bool          = False,
    ) -> dict:
        """Create Pinterest pin for Amazon product."""
        async def _run_async():
            # Fetch product from DB
            product = await _get_product(product_id)
            if not product:
                return {"status": "error", "reason": f"Product {product_id} not found"}

            from pinterest.pins import PinCreator
            from scraping.amazon.parser import ProductData
            product_data = ProductData(
                asin=product.asin,
                title=product.title or "",
                price=product.price,
                rating=float(product.rating) if product.rating else None,
                reviews_count=product.reviews_count,
                brand=product.brand or "",
                category=product.category or "",
                image_url=product.image_url or "",
                affiliate_link=product.affiliate_link or "",
                marketplace=product.marketplace or "amazon.fr",
            )

            creator = PinCreator()
            result  = await creator.pin_product(
                product=product_data,
                affiliate_url=affiliate_url or product.affiliate_link or "",
                force=force,
            )

            if result.success:
                # Update DB status
                await _update_pinterest_status(product_id, "product", result)
                # Notify
                if chat_id:
                    await _notify(chat_id,
                        f"📌 <b>Pin créé</b>\n"
                        f"🛍️ {product_data.title[:60]}\n"
                        f"🔗 {result.pinterest_url}"
                    )
            else:
                logger.warning(f"[pinterest] Pin failed: {result.error}")

            return {
                "status":      "success" if result.success else "failed",
                "pin_id":      result.pin_id,
                "pin_url":     result.pinterest_url,
                "board_id":    result.board_id,
                "error":       result.error,
            }

        try:
            return _run(_run_async())
        except PinterestError as e:
            if e.status_code == 429:
                raise self.retry(exc=e, countdown=60)
            if e.status_code == 401:
                return {"status": "error", "reason": "Invalid Pinterest token"}
            raise
        except Exception as e:
            if self.request.retries < self.max_retries:
                raise self.retry(exc=e, countdown=30)
            raise

    @celery_app.task(
        name="pinterest.daily_stats",
        queue="default",
    )
    def daily_stats_task() -> dict:
        """Get today's Pinterest pin statistics."""
        async def _run_async():
            from pinterest.boards import DailyScheduler
            scheduler = DailyScheduler()
            return await scheduler.get_stats()
        return _run(_run_async())

    @celery_app.task(
        name="pinterest.verify_token",
        queue="default",
    )
    def verify_token_task() -> dict:
        """Verify Pinterest access token is valid."""
        async def _run_async():
            from pinterest.client import get_pinterest_client
            client = get_pinterest_client()
            if not client.is_configured:
                return {"valid": False, "reason": "PINTEREST_ACCESS_TOKEN not set"}
            try:
                user = await client.get_user()
                return {"valid": True, "username": user.username}
            except Exception as e:
                return {"valid": False, "reason": str(e)[:100]}
        return _run(_run_async())

    return {
        "pin_product":   pin_product_task,
        "daily_stats":   daily_stats_task,
        "verify_token":  verify_token_task,
    }


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _get_product(product_id: str):
    try:
        import uuid
        from core.database import get_db
        from core.models.product import Product
        async with get_db() as db:
            return await db.get(Product, uuid.UUID(product_id))
    except Exception as e:
        logger.warning(f"[pinterest] Product fetch failed: {e}")
        return None


async def _update_pinterest_status(entity_id: str, entity_type: str, result) -> None:
    try:
        import uuid
        from core.database import get_db
        from core.models.product import Product, PublishStatus

        async with get_db() as db:
            obj = await db.get(Product, uuid.UUID(entity_id))

            if obj and hasattr(obj, "pinterest_status"):
                obj.pinterest_status = (
                    PublishStatus.PUBLISHED if result.success
                    else PublishStatus.FAILED
                )
                if result.pin_id and hasattr(obj, "pinterest_pin_id"):
                    obj.pinterest_pin_id = result.pin_id
    except Exception as e:
        logger.warning(f"[pinterest] DB update failed: {e}")


async def _notify(chat_id: int, message: str) -> None:
    try:
        import os, httpx
        token = os.environ.get("BOT_TOKEN", "")
        if not token: return
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            )
    except Exception as e:
        logger.warning(f"[pinterest] Notify failed: {e}")
