"""
publishing/tasks.py — Celery tasks for the publishing pipeline.

Orchestrates publishing to all platforms in parallel.
Each platform is independent: failure on one doesn't block others.

Tasks:
  publishing.publish_article  → publish to all configured platforms
  publishing.check_tinify     → check remaining TinyPNG credits

Flow:
  1. Load article from DB
  2. Compress image via TinyPNG (once, shared across platforms)
  3. Publish in parallel: WordPress + Blogger + Telegram + WhatsApp
  4. Update DB status per platform
  5. Notify admin Telegram

Retry per platform:
  HTTP 5xx → retry after 60s (max 3x)
  HTTP 401 → no retry (credentials issue)
  Timeout  → retry after 30s (max 2x)
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
    """Register all publishing Celery tasks."""

    @celery_app.task(
        name="publishing.publish_article",
        bind=True,
        max_retries=3,
        acks_late=True,
        queue="default",
    )
    def publish_article_task(
        self,
        entity_type:   str,           # "product"
        entity_id:     str,           # UUID
        title:         str,
        html_content:  str,
        image_url:     str            = "",
        slug:          str            = "",
        meta:          str            = "",
        tags:          list           = None,
        category:      str            = "",
        affiliate_url: str            = "",
        article_url:   str            = "",
        platforms:     list           = None,  # None = all configured
        chat_id:       Optional[int]  = None,
    ) -> dict:
        """
        Publish article to all configured platforms.

        Platforms: ["wordpress", "blogger", "telegram", "whatsapp"]
        Each platform runs independently (failure = logged, not fatal).
        """
        platforms = platforms or ["wordpress", "blogger", "telegram", "whatsapp"]

        async def _run_async():
            results = {}

            # Step 1: Compress image ONCE (shared)
            compressed_url = image_url
            compressed_bytes = None
            if image_url:
                try:
                    from publishing.image import get_tinify_client
                    tinify = get_tinify_client()
                    img_result = await tinify.compress_from_url(
                        image_url, download_result=True
                    )
                    compressed_url   = img_result.best_url
                    compressed_bytes = img_result.compressed_bytes
                    if img_result.was_compressed:
                        logger.info(
                            f"[publish] Image: -{img_result.ratio*100:.0f}% "
                            f"({img_result.saved_kb:.0f}KB saved)"
                        )
                except Exception as e:
                    logger.warning(f"[publish] Image compression failed: {e}")

            # Step 2: Publish in parallel
            tasks_map = {}

            if "wordpress" in platforms:
                from publishing.wordpress import WordPressPublisher
                wp = WordPressPublisher()
                if wp.is_configured:
                    tasks_map["wordpress"] = wp.publish_article(
                        title=title,
                        html_content=html_content,
                        image_url=image_url,
                        slug=slug,
                        meta_description=meta,
                        tags=tags or [],
                        category=category,
                        status="publish",
                        compress_image=False,  # Already compressed above
                    )

            if "blogger" in platforms:
                from publishing.publishers import BloggerPublisher
                bl = BloggerPublisher()
                if bl.is_configured:
                    tasks_map["blogger"] = bl.publish_article(
                        title=title,
                        html_content=html_content,
                        image_url=compressed_url,
                        labels=tags or [],
                        compress_image=False,
                    )

            if "telegram" in platforms:
                from publishing.publishers import TelegramPublisher
                tg = TelegramPublisher()
                if tg.is_configured:
                    tasks_map["telegram"] = tg.publish_article(
                        title=title,
                        excerpt=meta,
                        image_url=compressed_url,
                        article_url=article_url,
                        affiliate_url=affiliate_url,
                        tags=tags or [],
                        compress_image=False,
                    )

            if "whatsapp" in platforms:
                from publishing.publishers import WhatsAppPublisher
                wa = WhatsAppPublisher()
                if wa.is_configured:
                    tasks_map["whatsapp"] = wa.publish_article(
                        title=title,
                        excerpt=meta,
                        image_url=compressed_url,
                        article_url=article_url,
                        affiliate_url=affiliate_url,
                        compress_image=False,
                    )

            if not tasks_map:
                return {"status": "skipped", "reason": "No platforms configured"}

            # Run all in parallel
            platform_results = await asyncio.gather(
                *tasks_map.values(),
                return_exceptions=True,
            )

            for platform, result in zip(tasks_map.keys(), platform_results):
                if isinstance(result, Exception):
                    results[platform] = {"success": False, "error": str(result)}
                else:
                    results[platform] = {
                        "success": result.success,
                        "error":   result.error,
                        "url":     getattr(result, "post_url", None),
                        "id":      getattr(result, "post_id", None)
                                   or getattr(result, "message_id", None),
                    }
                logger.info(
                    f"[publish] {platform}: "
                    f"{'✅' if results[platform]['success'] else '❌'} "
                    f"{results[platform].get('url', results[platform].get('error', ''))}"
                )

            # Step 3: Update DB statuses
            await _update_publish_statuses(entity_type, entity_id, results)

            # Step 4: Notify admin
            success_count = sum(1 for r in results.values() if r["success"])
            if chat_id:
                urls_text = "\n".join(
                    f"  • {p}: {r.get('url','')}"
                    for p, r in results.items() if r.get("url")
                )
                await _notify(
                    chat_id=chat_id,
                    message=(
                        f"📢 <b>Article publié</b>\n"
                        f"📝 {title[:60]}\n"
                        f"✅ {success_count}/{len(results)} plateformes\n"
                        f"{urls_text}"
                    ),
                )

            return {
                "status":        "success" if success_count > 0 else "failed",
                "published":     success_count,
                "total":         len(results),
                "platforms":     results,
            }

        try:
            return _run(_run_async())
        except Exception as e:
            logger.error(f"[publish] Task error: {e}", exc_info=True)
            if self.request.retries < self.max_retries:
                raise self.retry(exc=e, countdown=60)
            raise

    @celery_app.task(
        name="publishing.check_tinify",
        queue="default",
    )
    def check_tinify_task() -> dict:
        """Check remaining TinyPNG credits and notify if low."""
        async def _check():
            from publishing.image import get_tinify_client
            client   = get_tinify_client()
            credits  = await client.get_remaining_credits()
            logger.info(f"[tinify] Credits remaining: {credits}")
            if credits is not None and credits < 50:
                logger.warning(f"[tinify] ⚠️ Low credits: {credits} remaining")
            return {"credits_remaining": credits}

        return _run(_check())

    return {
        "publish_article":  publish_article_task,
        "check_tinify":     check_tinify_task,
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _update_publish_statuses(
    entity_type: str,
    entity_id:   str,
    results:     dict,
) -> None:
    """Update publish status in DB for each platform."""
    try:
        import uuid
        from core.database import get_db

        STATUS_MAP = {True: "PUBLISHED", False: "FAILED"}

        async with get_db() as db:
            from core.models.product import Product, PublishStatus
            obj = await db.get(Product, uuid.UUID(entity_id))

            if not obj:
                return

            for platform, result in results.items():
                status_val = (
                    PublishStatus.PUBLISHED if result["success"]
                    else PublishStatus.FAILED
                )
                field_map = {
                    "wordpress": "wp_status",
                    "blogger":   "blogger_status",
                    "telegram":  "telegram_status",
                    "whatsapp":  "whatsapp_status",
                }
                field = field_map.get(platform)
                if field and hasattr(obj, field):
                    setattr(obj, field, status_val)
                    if result.get("url") and hasattr(obj, f"{platform[:2]}_url"):
                        setattr(obj, f"{platform[:2]}_url", result["url"])

    except Exception as e:
        logger.warning(f"[publish] DB status update failed: {e}")


async def _notify(chat_id: int, message: str) -> None:
    """Send Telegram notification to admin."""
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
        logger.warning(f"[publish] Notify failed: {e}")
