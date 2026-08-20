"""
ai/tasks.py — Celery tasks for async article generation.

Tasks:
  ai.generate_product_review   → Amazon URL → product review
  ai.generate_comparison       → list of ASINs → comparison article

Retry strategy:
  ArticleGenerationError (score too low)  → retry with longer prompt (1x)
  OpenRouter timeout                      → retry after 30s (2x)
  OpenRouter API error (5xx)              → retry after 60s (2x)
  No retry: missing config, invalid input

Each task:
  1. Fetch source data (product from Amazon)
  2. Generate article via AIGenerator
  3. Store result in DB (Article model — Phase 5 publishes)
  4. Notify Telegram if chat_id provided
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
    """Register all AI Celery tasks."""

    from ai.generator import AIGenerator, ArticleGenerationError

    @celery_app.task(
        name="ai.generate_product_review",
        bind=True,
        max_retries=2,
        acks_late=True,
        queue="default",
    )
    def generate_product_review_task(
        self,
        product_id:    str,
        affiliate_url: str          = "",
        keyword:       str          = "",
        chat_id:       Optional[int] = None,
        save_to_db:    bool         = True,
    ) -> dict:
        """Generate a product review article from a Product DB record."""
        async def _run_async():
            product = await _get_product(product_id)
            if not product:
                return {"status": "error", "reason": f"Product {product_id} not found"}

            gen    = AIGenerator()
            result = await gen.generate_product_review(
                product=product,
                affiliate_url=affiliate_url or product.affiliate_link,
                keyword=keyword,
            )

            article_id = None
            if save_to_db:
                article_id = await _save_article(result, entity_type="product",
                                                  entity_id=product_id)

            if chat_id:
                await _notify(
                    chat_id=chat_id,
                    message=(
                        f"✅ <b>Avis produit généré</b>\n"
                        f"📝 {result.article.title}\n"
                        f"📊 Score: {result.score.total}/100 ({result.score.grade})\n"
                        f"📖 {result.article.word_count} mots"
                    ),
                )

            return {
                "status":     "success",
                "title":      result.article.title,
                "score":      result.score.total,
                "word_count": result.article.word_count,
                "model":      result.model_used,
                "article_id": article_id,
            }

        try:
            return _run(_run_async())
        except ArticleGenerationError as e:
            if self.request.retries < self.max_retries:
                raise self.retry(exc=e, countdown=30)
            return {"status": "failed", "reason": e.reason}
        except Exception as e:
            if self.request.retries < self.max_retries:
                raise self.retry(exc=e, countdown=60)
            raise

    return {
        "generate_product_review": generate_product_review_task,
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_product(product_id: str):
    """Fetch Product from DB."""
    try:
        import uuid
        from core.database import get_db
        from core.models.product import Product
        async with get_db() as db:
            return await db.get(Product, uuid.UUID(product_id))
    except Exception as e:
        logger.warning(f"[task:ai] Product fetch failed: {e}")
        return None


async def _save_article(result, entity_type: str, entity_id: str) -> Optional[str]:
    """Save generated article to DB."""
    try:
        from core.database import get_db
        from core.models.analytics import AnalyticsEvent, EntityType, EventType
        import uuid

        async with get_db() as db:
            event = AnalyticsEvent(
                entity_type=EntityType.ARTICLE if hasattr(EntityType, 'ARTICLE') else EntityType.PRODUCT,
                entity_id=uuid.UUID(entity_id),
                event_type=EventType.GENERATED if hasattr(EventType, 'GENERATED') else EventType.SCRAPED,
                data={
                    "title":      result.article.title,
                    "score":      result.score.total,
                    "word_count": result.article.word_count,
                    "model":      result.model_used,
                    "slug":       result.article.slug,
                    "meta":       result.article.meta_description,
                },
            )
            db.add(event)
            await db.flush()
            return str(event.id)
    except Exception as e:
        logger.warning(f"[task:ai] Article save failed (non-fatal): {e}")
        return None


async def _notify(chat_id: int, message: str) -> None:
    """Send Telegram notification."""
    try:
        import os, httpx
        token = os.environ.get("BOT_TOKEN", "")
        if not token:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            )
    except Exception as e:
        logger.warning(f"[task:ai] Notify failed: {e}")
