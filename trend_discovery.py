"""
trend_discovery.py -- Discover trending Amazon products using Google Trends.

Free, no API key required. Uses pytrends for Google Trends data.
Called by daily_scheduler.py before each publishing session.

Env vars: none required (uses default categories if AUTO_KEYWORDS not set).
"""
from __future__ import annotations

import logging
import random
import time
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Product categories for trend discovery (expandable)
PRODUCT_CATEGORIES = [
    "wireless earbuds", "robot vacuum", "air fryer", "standing desk",
    "mechanical keyboard", "portable charger", "smart watch", "coffee maker",
    "bluetooth speaker", "electric toothbrush", "desk lamp", "webcam",
    "smart plug", "security camera", "fitness tracker", "power bank",
    "laptop stand", "monitor light bar", "noise cancelling headphones",
    "instant pot", "cordless vacuum", "air purifier",
]


def get_trending_amazon_keywords(categories: list = None, top_n: int = 5) -> list[str]:
    """
    Get trending product keywords from Google Trends (past 7 days, US).
    Returns list of keyword strings ready for Amazon search.
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.warning("[trends] pytrends not installed — pip install pytrends")
        return []

    pytrends = TrendReq(hl="en-US", tz=360)
    trending_keywords = []
    cats = categories or random.sample(PRODUCT_CATEGORIES, min(3, len(PRODUCT_CATEGORIES)))

    for category in cats:
        try:
            pytrends.build_payload([category], timeframe="now 7-d", geo="US")
            related = pytrends.related_queries()

            if related.get(category) and related[category].get("top") is not None:
                top_queries = related[category]["top"]["query"].tolist()[:3]
                trending_keywords.extend(top_queries)

            time.sleep(random.uniform(2, 4))
        except Exception as e:
            logger.info(f"[trends] Failed for '{category}': {e}")
            continue

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for kw in trending_keywords:
        kw_lower = kw.lower().strip()
        if kw_lower not in seen and len(kw_lower) > 3:
            seen.add(kw_lower)
            unique.append(kw)

    return unique[:top_n]


def build_amazon_search_url(keyword: str, affiliate_tag: str = "dazzledeals00-20") -> str:
    """Build an Amazon search URL with affiliate tag."""
    return f"https://www.amazon.com/s?k={quote(keyword)}&tag={affiliate_tag}"


def get_trending_products_for_scheduler() -> list[str]:
    """
    Main entry point for daily_scheduler.py.
    Returns list of Amazon search URLs with trending keywords.
    """
    keywords = get_trending_amazon_keywords(top_n=5)
    if not keywords:
        logger.info("[trends] No trending keywords found")
        return []
    urls = [build_amazon_search_url(kw) for kw in keywords]
    logger.info(f"[trends] Found {len(urls)} trending products: {keywords}")
    return urls


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    urls = get_trending_products_for_scheduler()
    for u in urls:
        print(u)
