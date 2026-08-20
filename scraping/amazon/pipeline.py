"""
scraping/amazon/pipeline.py — Complete Amazon product scraping pipeline.

Pipeline stages:
  1. URL validation     → is it an Amazon URL?
  2. ASIN resolution    → extract ASIN, normalize URL, detect marketplace
  3. DB dedup check     → already in DB? return cached record
  4. HTML fetch         → HybridFetchEngine (6 layers)
  5. Data extraction    → MultiStrategyExtractor → Amazon parser
  6. Validation         → quality filters (rating, reviews, availability)
  7. DB persistence     → upsert Product model
  8. Analytics logging  → record SCRAPED event

The pipeline is designed for Celery task execution:
  - All async operations
  - Typed exceptions for retry decisions
  - Structured logging for monitoring
  - Returns (ProductData, Product) for downstream tasks

Error handling per stage:
  Stage 1 → InvalidURLError (no retry)
  Stage 2 → ASINNotFoundError (no retry)
  Stage 3 → (no error, return cached)
  Stage 4 → ScrapingBlockedError (retry with different method)
           → ScrapingTimeoutError (retry same method)
           → ScrapingAllMethodsFailedError (escalate, human check)
  Stage 5 → ScrapingParseError (snapshot saved, no retry)
  Stage 6 → ProductRejectedError (no retry)
  Stage 7 → DatabaseError (retry — transient)
  Stage 8 → (non-fatal, logged)
"""
from __future__ import annotations

import logging
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.models.product import Product

from core.exceptions import (
    ASINNotFoundError,
    DatabaseError,
    InvalidURLError,
    ProductRejectedError,
    ScrapingAllMethodsFailedError,
    ScrapingBlockedError,
    ScrapingParseError,
    ScrapingTimeoutError,
)

logger = logging.getLogger(__name__)


# ── Inline imports (avoid circular) ───────────────────────────────────────────
def _get_resolver():
    from scraping.amazon.resolver import resolve_amazon_url, is_amazon_url
    return resolve_amazon_url, is_amazon_url

def _get_engine():
    from scraping.fetch.engine import HybridFetchEngine
    return HybridFetchEngine

def _get_extractor():
    from scraping.amazon.parser import AmazonParser
    return AmazonParser

def _get_metrics():
    from scraping.metrics import get_metrics
    return get_metrics()


# ── Public pipeline entry point ───────────────────────────────────────────────

async def run_amazon_pipeline(
    url:            str,
    affiliate_tag:  str          = "",
    save_to_db:     bool         = True,
    force_refresh:  bool         = False,
    chat_id:        Optional[int] = None,
    min_rating:     float        = 3.5,
    min_reviews:    int          = 10,
) -> tuple["ProductData", Optional["Product"]]:
    """
    Run the complete Amazon product pipeline.

    Args:
        url:           Amazon product URL (any format — canonical, short, affiliate)
        affiliate_tag: Override affiliate tag (uses AFFILIATE_TAG env var if empty)
        save_to_db:    Persist to PostgreSQL (set False for dry runs)
        force_refresh: Skip HTML cache, re-scrape even if cached
        chat_id:       Telegram chat ID for job tracking
        min_rating:    Minimum rating to accept (reject below this)
        min_reviews:   Minimum review count to accept

    Returns:
        Tuple of (ProductData, Product DB record or None)

    Raises:
        InvalidURLError:              Not an Amazon URL
        ASINNotFoundError:            Cannot extract ASIN
        ScrapingBlockedError:         Blocked by bot detection
        ScrapingAllMethodsFailedError: All fetch methods failed
        ScrapingParseError:           HTML fetched but extraction failed
        ProductRejectedError:         Quality filter rejected product
        DatabaseError:                DB operation failed (retryable)
    """
    start = time.monotonic()
    resolve_amazon_url, is_amazon_url = _get_resolver()

    # ── Stage 1: Validate URL ─────────────────────────────────────────────
    if not url or not url.strip():
        raise InvalidURLError(url, "URL is empty")
    if not is_amazon_url(url.strip()):
        raise InvalidURLError(url, "Not a recognized Amazon URL or ASIN")

    logger.info(f"[pipeline] ▶ Start: {url[:80]}")

    # ── Stage 2: Resolve URL + ASIN ───────────────────────────────────────
    import os
    aff_tag   = affiliate_tag or os.environ.get("AFFILIATE_TAG","")

    amazon_url = await resolve_amazon_url(url, affiliate_tag=aff_tag)
    if not amazon_url:
        raise ASINNotFoundError(url)

    logger.info(
        f"[pipeline] ASIN={amazon_url.asin} "
        f"marketplace={amazon_url.marketplace}"
        + (" (was short URL)" if amazon_url.was_shortened else "")
    )

    # ── Stage 3: DB deduplication check ──────────────────────────────────
    if save_to_db and not force_refresh:
        existing = await _fetch_existing(amazon_url.asin)
        if existing and existing.title:
            elapsed = time.monotonic() - start
            logger.info(
                f"[pipeline] DB cache hit: ASIN={amazon_url.asin} "
                f"({elapsed:.2f}s)"
            )
            from scraping.amazon.parser import ProductData
            product_data = _model_to_data(existing, amazon_url)
            return product_data, existing

    # ── Stage 4: Fetch HTML ───────────────────────────────────────────────
    HybridFetchEngine = _get_engine()
    engine = HybridFetchEngine(
        content_type="product",
        enable_playwright=True,
        skip_cache=force_refresh,
    )
    fetch_result = await engine.fetch(amazon_url.canonical_url)

    if not fetch_result.success:
        if fetch_result.is_blocked:
            raise ScrapingBlockedError(
                url=amazon_url.canonical_url,
                method=fetch_result.method,
                status_code=fetch_result.status_code,
            )
        if fetch_result.error_type == "timeout":
            raise ScrapingTimeoutError(
                url=amazon_url.canonical_url,
                timeout=25,
                method=fetch_result.method,
            )
        raise ScrapingAllMethodsFailedError(
            url=amazon_url.canonical_url,
            methods_tried=[fetch_result.method or "all"],
        )

    # ── Stage 5: Extract product data ─────────────────────────────────────
    AmazonParser = _get_extractor()
    parser  = AmazonParser()
    product_data = parser.parse(
        html=fetch_result.html,
        asin=amazon_url.asin,
        marketplace=amazon_url.marketplace,
        affiliate_link=amazon_url.affiliate_url,
        original_url=url,
        scrape_method=fetch_result.method,
    )

    if not product_data or not product_data.is_valid():
        # Save snapshot for debugging
        if fetch_result.html:
            from scraping.cache.html_cache import get_html_cache
            await get_html_cache().save_snapshot(
                url=amazon_url.canonical_url,
                html=fetch_result.html,
                reason="extraction_failed",
            )
        raise ScrapingParseError(
            url=amazon_url.canonical_url,
            reason="Extraction returned empty or invalid ProductData",
        )

    metrics = _get_metrics()
    metrics.record_parse_success(amazon_url.marketplace)

    # ── Stage 6: Quality validation ───────────────────────────────────────
    passes, reason = product_data.passes_quality_filter(min_rating, min_reviews)
    if not passes:
        raise ProductRejectedError(
            asin=amazon_url.asin,
            reason=reason,
            values={
                "rating":    product_data.rating,
                "reviews":   product_data.reviews_count,
                "available": product_data.is_available,
            },
        )

    elapsed = time.monotonic() - start
    logger.info(
        f"[pipeline] ✅ Extracted: {product_data.title[:60]!r} "
        f"| price={product_data.price} rating={product_data.rating} "
        f"| {fetch_result.method} | {elapsed:.1f}s"
    )

    # ── Stage 7: Persist to PostgreSQL ────────────────────────────────────
    db_record = None
    if save_to_db:
        try:
            db_record = await _upsert_product(product_data, amazon_url)
            logger.info(f"[pipeline] DB saved: product_id={db_record.id}")
        except Exception as e:
            logger.error(f"[pipeline] DB save failed (non-fatal): {e}")

    # ── Stage 8: Analytics ────────────────────────────────────────────────
    await _log_analytics(product_data, db_record, fetch_result.method, fetch_result.latency_ms)

    return product_data, db_record


# ── DB Helpers ────────────────────────────────────────────────────────────────

async def _fetch_existing(asin: str) -> Optional["Product"]:
    try:
        from sqlalchemy import select
        from core.database import get_db
        from core.models.product import Product
        async with get_db() as db:
            r = await db.execute(select(Product).where(Product.asin == asin))
            return r.scalar_one_or_none()
    except Exception as e:
        logger.warning(f"[pipeline] DB lookup failed: {e}")
        return None


async def _upsert_product(product_data, amazon_url) -> "Product":
    from sqlalchemy import select
    from core.database import get_db
    from core.models.product import Product, ProductStatus, PublishStatus

    async with get_db() as db:
        r    = await db.execute(select(Product).where(Product.asin == product_data.asin))
        prod = r.scalar_one_or_none()

        if prod is None:
            prod = Product(
                asin=product_data.asin,
                status=ProductStatus.NEW,
                telegram_status=PublishStatus.PENDING,
                wp_status=PublishStatus.PENDING,
                blogger_status=PublishStatus.PENDING,
                pinterest_status=PublishStatus.PENDING,
                whatsapp_status=PublishStatus.PENDING,
            )
            db.add(prod)

        prod.marketplace      = product_data.marketplace
        prod.affiliate_link   = product_data.affiliate_link
        prod.original_url     = product_data.original_url
        prod.title            = product_data.title
        prod.short_description= product_data.short_description
        prod.brand            = product_data.brand
        prod.category         = product_data.category
        prod.price            = product_data.price
        prod.old_price        = product_data.old_price
        prod.discount_pct     = product_data.discount_pct
        prod.currency         = product_data.currency
        prod.rating           = product_data.rating
        prod.reviews_count    = product_data.reviews_count
        prod.is_available     = product_data.is_available
        prod.is_bestseller    = product_data.is_bestseller
        prod.bestseller_rank  = product_data.bestseller_rank
        prod.coupon_text      = product_data.coupon_text
        prod.image_url        = product_data.image_url
        prod.scrape_method    = product_data.scrape_method

        await db.flush()
        await db.refresh(prod)
        return prod


def _model_to_data(prod, amazon_url):
    from scraping.amazon.parser import ProductData
    return ProductData(
        asin=prod.asin,
        marketplace=prod.marketplace or amazon_url.marketplace,
        affiliate_link=prod.affiliate_link or amazon_url.affiliate_url,
        original_url=prod.original_url or "",
        title=prod.title or "",
        short_description=prod.short_description or "",
        brand=prod.brand or "",
        category=prod.category or "",
        price=prod.price,
        old_price=prod.old_price,
        discount_pct=prod.discount_pct,
        currency=prod.currency or "EUR",
        rating=float(prod.rating) if prod.rating else None,
        reviews_count=prod.reviews_count,
        is_available=prod.is_available,
        is_bestseller=prod.is_bestseller,
        bestseller_rank=prod.bestseller_rank or "",
        coupon_text=prod.coupon_text or "",
        image_url=prod.image_url or "",
    )


async def _log_analytics(product_data, db_record, method, latency_ms) -> None:
    if not db_record:
        return
    try:
        from core.database import get_db
        from core.models.analytics import AnalyticsEvent, EntityType, EventType
        async with get_db() as db:
            db.add(AnalyticsEvent(
                entity_type=EntityType.PRODUCT,
                entity_id=db_record.id,
                event_type=EventType.SCRAPED,
                data={
                    "method":     method,
                    "latency_ms": round(latency_ms, 1),
                    "asin":       product_data.asin,
                    "price":      str(product_data.price) if product_data.price else None,
                },
            ))
    except Exception as e:
        logger.warning(f"[pipeline] Analytics failed (non-fatal): {e}")
