"""
scraping/amazon/tasks.py — Celery tasks for the Amazon pipeline.

Architecture decisions:
  - Each pipeline stage is a separate Celery task (composable).
  - Tasks use Celery's retry mechanism with exponential backoff.
  - Exception types determine retry strategy:
      ScrapingBlockedError → retry with longer delay (bot detection needs time)
      ScrapingTimeoutError → retry immediately (transient network issue)
      ProductRejectedError → NO retry (quality filter is deterministic)
      DatabaseError        → retry with short delay (transient DB issue)
  - All tasks are async-wrapped: asyncio.run() inside sync Celery task.
    Celery workers are sync; we run async code with a fresh event loop.
  - Job model updated at each stage for real-time status tracking.
  - chat_id passed through for Telegram notification on completion.

Queue configuration:
  amazon_full    → default queue (medium priority)
  amazon_scrape  → default queue
  amazon_bulk    → low_priority queue (background)

Celery best practices applied:
  - task_acks_late=True: task only acked after completion (not on receive)
  - task_reject_on_worker_lost=True: re-queue if worker crashes mid-task
  - max_retries: set per exception type
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
            # Running in an existing event loop (e.g. tests)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _get_celery():
    """Import Celery app lazily."""
    from scheduler.celery_app import celery_app
    return celery_app


# ── Full Amazon pipeline task ─────────────────────────────────────────────────

def register_tasks(celery_app):
    """
    Register all Amazon Celery tasks.

    Called from scheduler/celery_app.py:
        from scraping.amazon.tasks import register_tasks
        register_tasks(celery_app)
    """

    @celery_app.task(
        name="amazon.scrape_product",
        bind=True,
        max_retries=3,
        default_retry_delay=60,
        acks_late=True,
        reject_on_worker_lost=True,
        queue="default",
    )
    def scrape_product_task(
        self,
        url:            str,
        affiliate_tag:  str          = "",
        save_to_db:     bool         = True,
        force_refresh:  bool         = False,
        chat_id:        Optional[int] = None,
        job_id:         Optional[str] = None,
        min_rating:     float        = 3.5,
        min_reviews:    int          = 10,
    ) -> dict:
        """
        Celery task: run full Amazon product pipeline.

        Returns dict with product data on success.
        Raises for permanent failures (no retry).
        Retries for transient failures.
        """
        from core.exceptions import (
            ScrapingBlockedError, ScrapingTimeoutError,
            ProductRejectedError, ScrapingAllMethodsFailedError,
            ScrapingParseError, DatabaseError,
        )
        from scraping.amazon.pipeline import run_amazon_pipeline

        logger.info(
            f"[task:amazon] scrape_product: url={url[:60]} "
            f"attempt={self.request.retries + 1}/{self.max_retries + 1}"
        )

        # Update job status in DB
        if job_id:
            _run(_update_job_status(job_id, "running"))

        try:
            product_data, db_record = _run(run_amazon_pipeline(
                url=url,
                affiliate_tag=affiliate_tag,
                save_to_db=save_to_db,
                force_refresh=force_refresh,
                chat_id=chat_id,
                min_rating=min_rating,
                min_reviews=min_reviews,
            ))

            result = {
                "status":       "success",
                "asin":         product_data.asin,
                "title":        product_data.title[:100],
                "price":        str(product_data.price) if product_data.price else None,
                "rating":       product_data.rating,
                "reviews":      product_data.reviews_count,
                "image_url":    product_data.image_url,
                "product_id":   db_record.id if db_record else None,
                "method":       product_data.scrape_method,
            }

            if job_id:
                _run(_update_job_status(job_id, "success", result=result))

            # Notify Telegram if chat_id provided
            if chat_id:
                _run(_notify_telegram(
                    chat_id=chat_id,
                    message=(
                        f"✅ <b>Produit extrait</b>\n"
                        f"<b>{product_data.title[:60]}</b>\n"
                        f"💰 {product_data.price} {product_data.currency}\n"
                        f"⭐ {product_data.rating}/5 ({product_data.reviews_count} avis)"
                    ),
                ))

            return result

        except ProductRejectedError as e:
            # Quality filter: don't retry
            logger.warning(f"[task:amazon] Product rejected: {e.message}")
            if job_id:
                _run(_update_job_status(job_id, "failed",
                                        error=e.message, error_type="rejected"))
            if chat_id:
                _run(_notify_telegram(chat_id,
                    f"⚠️ Produit rejeté: {e.message}"))
            return {"status": "rejected", "reason": e.message}

        except ScrapingBlockedError as e:
            # Bot detection: retry with longer delay
            delay = 120 * (2 ** self.request.retries)  # 120s, 240s, 480s
            logger.warning(
                f"[task:amazon] Blocked, retry in {delay}s: {e.message}"
            )
            if job_id:
                _run(_update_job_status(job_id, "retrying", error=e.message))
            raise self.retry(exc=e, countdown=delay)

        except ScrapingTimeoutError as e:
            # Timeout: retry sooner
            delay = 30 * (2 ** self.request.retries)
            logger.warning(f"[task:amazon] Timeout, retry in {delay}s")
            raise self.retry(exc=e, countdown=delay)

        except (ScrapingAllMethodsFailedError, ScrapingParseError) as e:
            # All methods failed or parse error: retry with long delay
            delay = 300  # 5 minutes
            logger.error(f"[task:amazon] All methods failed: {e.message}")
            if self.request.retries >= self.max_retries:
                if job_id:
                    _run(_update_job_status(job_id, "exhausted", error=e.message))
                return {"status": "failed", "reason": e.message}
            raise self.retry(exc=e, countdown=delay)

        except DatabaseError as e:
            # DB error: retry quickly
            delay = 10 * (2 ** self.request.retries)
            logger.error(f"[task:amazon] DB error: {e.message}")
            raise self.retry(exc=e, countdown=delay)

        except Exception as e:
            logger.error(f"[task:amazon] Unexpected error: {e}", exc_info=True)
            if job_id:
                _run(_update_job_status(job_id, "failed", error=str(e)[:200]))
            raise

    @celery_app.task(
        name="amazon.scrape_bulk",
        bind=True,
        max_retries=0,
        acks_late=True,
        queue="low_priority",
    )
    def scrape_bulk_task(
        self,
        urls:          list[str],
        affiliate_tag: str  = "",
        max_concurrent:int  = 2,
        chat_id:       Optional[int] = None,
    ) -> dict:
        """
        Celery task: scrape multiple Amazon URLs.

        Runs concurrently with max_concurrent limit.
        Returns summary of successes and failures.
        """
        from scraping.amazon.pipeline import run_amazon_pipeline

        async def _run_bulk():
            sem = asyncio.Semaphore(max_concurrent)
            results = {"success": 0, "rejected": 0, "failed": 0, "errors": []}

            async def _one(url):
                async with sem:
                    try:
                        await run_amazon_pipeline(url=url, affiliate_tag=affiliate_tag)
                        results["success"] += 1
                    except ProductRejectedError:
                        results["rejected"] += 1
                    except Exception as e:
                        results["failed"] += 1
                        results["errors"].append(f"{url[:50]}: {str(e)[:80]}")

            from core.exceptions import ProductRejectedError
            await asyncio.gather(*[_one(u) for u in urls])
            return results

        summary = _run(_run_bulk())
        logger.info(f"[task:amazon] Bulk complete: {summary}")

        if chat_id:
            _run(_notify_telegram(
                chat_id=chat_id,
                message=(
                    f"✅ <b>Extraction en lot terminée</b>\n"
                    f"  ✅ Succès: {summary['success']}\n"
                    f"  ⚠️ Rejetés: {summary['rejected']}\n"
                    f"  ❌ Échecs: {summary['failed']}"
                ),
            ))

        return summary

    @celery_app.task(
        name="amazon.search_keyword",
        bind=True,
        max_retries=2,
        acks_late=True,
        queue="default",
    )
    def search_keyword_task(
        self,
        keyword:     str,
        marketplace: str  = "amazon.fr",
        max_results: int  = 20,
        min_rating:  float = 3.5,
        min_reviews: int  = 10,
        auto_process:bool  = False,
        chat_id:     Optional[int] = None,
    ) -> dict:
        """
        Celery task: search Amazon keyword and optionally auto-process results.

        If auto_process=True, triggers scrape_product_task for each result.
        """
        from scraping.amazon.search import AmazonSearchEngine

        async def _run_search():
            engine = AmazonSearchEngine(
                marketplace=marketplace,
                min_rating=min_rating,
                min_reviews=min_reviews,
            )
            return await engine.search(keyword, max_results=max_results)

        results = _run(_run_search())
        summary = {
            "keyword":  keyword,
            "found":    len(results),
            "asins":    [r.asin for r in results[:20]],
            "titles":   [r.title[:60] for r in results[:5]],
        }

        if auto_process:
            for result in results:
                url = f"https://www.{marketplace}/dp/{result.asin}"
                scrape_product_task.apply_async(
                    kwargs={"url": url, "chat_id": chat_id},
                    queue="default",
                )
            summary["auto_processing"] = len(results)

        return summary

    return {
        "scrape_product": scrape_product_task,
        "scrape_bulk":    scrape_bulk_task,
        "search_keyword": search_keyword_task,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _update_job_status(
    job_id: str,
    status: str,
    result: Optional[dict] = None,
    error:  Optional[str]  = None,
    error_type: str        = "",
) -> None:
    """Update Job status in PostgreSQL."""
    try:
        import uuid
        from sqlalchemy import select
        from core.database import get_db
        from core.models.job import Job, JobStatus

        job_uuid = uuid.UUID(job_id)
        status_map = {
            "running":   JobStatus.RUNNING,
            "success":   JobStatus.SUCCESS,
            "failed":    JobStatus.FAILED,
            "retrying":  JobStatus.RETRYING,
            "exhausted": JobStatus.EXHAUSTED,
        }

        async with get_db() as db:
            j = await db.get(Job, job_uuid)
            if j:
                j.status = status_map.get(status, JobStatus.FAILED)
                if result:
                    j.result = result
                if error:
                    j.error_message = error[:500]
                    j.error_type    = error_type or "scraping"
    except Exception as e:
        logger.warning(f"Job status update failed: {e}")


async def _notify_telegram(chat_id: int, message: str) -> None:
    """Send Telegram notification (non-blocking)."""
    try:
        import os
        import httpx
        token = os.environ.get("BOT_TOKEN", "")
        if not token:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id":    chat_id,
                    "text":       message,
                    "parse_mode": "HTML",
                },
            )
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")
