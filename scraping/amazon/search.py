"""
scraping/amazon/search.py — Amazon keyword search and product discovery.

Architecture decisions:
  - Search results are extracted from Amazon search HTML (not an API).
  - Multi-strategy extraction: embedded JSON (search results are in JSON) + DOM.
  - Results are filtered by quality thresholds before returning.
  - Trending/bestseller detection from search result badges.
  - Pagination support for fetching multiple pages.
  - Cache TTL for search is short (20min) — results change frequently.
  - Rate limiting applied per search (domain = amazon.fr).

Extracted per result:
  - ASIN, title, image, price, rating, reviews, badges

Filtering:
  - min_rating: reject products below threshold
  - min_reviews: reject products with too few reviews
  - require_image: reject results without product image
  - available_only: reject out-of-stock items

Search URL formats:
  - Keyword: https://www.amazon.fr/s?k={keyword}
  - Category: https://www.amazon.fr/s?k={keyword}&rh=n:{category_id}
  - Bestseller: https://www.amazon.fr/best-sellers-kitchen/{cat}/
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional
from urllib.parse import urlencode, quote

logger = logging.getLogger(__name__)


# ── Search Result ─────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    """A single product from Amazon search results."""
    asin:         str
    title:        str
    image_url:    str              = ""
    price:        Optional[Decimal] = None
    old_price:    Optional[Decimal] = None
    rating:       Optional[float]  = None
    rating_count: Optional[int]    = None
    is_bestseller: bool            = False
    is_amazon_choice: bool         = False
    is_prime:     bool             = False
    product_url:  str              = ""
    marketplace:  str              = "amazon.fr"

    @property
    def quality_score(self) -> float:
        """Score 0-100 for ranking search results."""
        score = 0.0
        if self.rating:
            score += self.rating * 15      # Max 75 (rating 5.0 × 15)
        if self.rating_count:
            import math
            score += min(25, math.log10(max(1, self.rating_count)) * 10)
        if self.is_bestseller:
            score += 10
        if self.is_amazon_choice:
            score += 5
        if self.image_url:
            score += 5
        return round(score, 1)

    def passes_filter(
        self,
        min_rating:    float = 3.5,
        min_reviews:   int   = 10,
        require_image: bool  = True,
    ) -> bool:
        if require_image and not self.image_url:
            return False
        if self.rating is not None and self.rating < min_rating:
            return False
        if self.rating_count is not None and self.rating_count < min_reviews:
            return False
        return True


# ── URL Builder ───────────────────────────────────────────────────────────────

def build_search_url(
    keyword:     str,
    marketplace: str = "amazon.fr",
    page:        int = 1,
    sort_by:     str = "relevanceblender",
) -> str:
    """
    Build Amazon search URL.

    sort_by options:
      "relevanceblender" → Most relevant
      "review-rank"       → Average customer review
      "date-desc-rank"    → Newest arrivals
      "-price"            → Price: Low to High
      "price"             → Price: High to Low
    """
    params = {
        "k":   keyword,
        "ref": "sr_pg_" + str(page),
    }
    if page > 1:
        params["page"] = str(page)
    if sort_by != "relevanceblender":
        params["s"] = sort_by

    query = urlencode(params)
    return f"https://www.{marketplace}/s?{query}"


def build_bestseller_url(
    category_node: str,
    marketplace:   str = "amazon.fr",
    page:          int = 1,
) -> str:
    """Build Amazon Best Sellers URL for a category node."""
    base = f"https://www.{marketplace}/best-sellers"
    if category_node:
        base += f"/zgbs/{category_node}"
    if page > 1:
        base += f"?pg={page}"
    return base


# ── Search Parser ─────────────────────────────────────────────────────────────

def _parse_price(text: str) -> Optional[Decimal]:
    if not text:
        return None
    cleaned = re.sub(r"[€$£\s]", "", text)
    cleaned = re.sub(r"[^\d,.]", "", cleaned)
    if not cleaned:
        return None
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _parse_rating(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"(\d+[,.]?\d*)", text)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            pass
    return None


def _parse_count(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"[\d,\s]+", text)
    if m:
        try:
            return int(re.sub(r"[\s,]", "", m.group()))
        except ValueError:
            pass
    return None


def _extract_results_from_html(
    html:        str,
    marketplace: str = "amazon.fr",
) -> list[SearchResult]:
    """
    Extract search results from Amazon search page HTML.

    Uses multiple strategies:
      1. Parse search result JSON embedded in page
      2. Parse DOM search result cards
    """
    results = []

    # Strategy 1: Try to extract from embedded JSON
    json_results = _extract_from_json(html, marketplace)
    if json_results:
        results.extend(json_results)

    # Strategy 2: DOM parsing as fallback/supplement
    dom_results = _extract_from_dom(html, marketplace)

    # Merge: add DOM results not already in JSON results
    existing_asins = {r.asin for r in results}
    for r in dom_results:
        if r.asin not in existing_asins:
            results.append(r)
            existing_asins.add(r.asin)

    return results


def _extract_from_json(html: str, marketplace: str) -> list[SearchResult]:
    """
    Amazon embeds search results in JSON scripts.
    Look for patterns like: data-asin="B08XYZ123" in JSON context.
    """
    results = []
    # Amazon search results have data-asin attributes in JSON
    asin_pattern = re.compile(
        r'"asin"\s*:\s*"([A-Z0-9]{10})"'
        r'.*?"title"\s*:\s*\{[^}]*"raw"\s*:\s*"([^"]+)"',
        re.DOTALL
    )
    for match in asin_pattern.finditer(html[:500000]):  # Limit scan
        try:
            asin  = match.group(1)
            title = match.group(2)
            results.append(SearchResult(
                asin=asin,
                title=title[:200],
                marketplace=marketplace,
            ))
        except Exception:
            continue
    return results[:50]


def _extract_from_dom(html: str, marketplace: str) -> list[SearchResult]:
    """Parse Amazon search result cards from DOM."""
    from bs4 import BeautifulSoup
    results = []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return results

    # Amazon search result containers
    CARD_SELECTORS = [
        "[data-asin][data-component-type='s-search-result']",
        "div.s-result-item[data-asin]",
        ".s-card-container[data-asin]",
        "[data-asin]:not([data-asin=''])",
    ]

    cards = []
    for sel in CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            break

    for card in cards[:50]:
        asin = card.get("data-asin", "").strip()
        if not asin or len(asin) != 10:
            continue

        result = SearchResult(asin=asin, marketplace=marketplace)

        # Title
        for title_sel in [
            "h2 a span", ".a-size-base-plus", ".a-size-medium",
            "[class*='product-title']", "h2 span"
        ]:
            el = card.select_one(title_sel)
            if el:
                result.title = el.get_text(strip=True)[:200]
                break

        if not result.title:
            continue  # Skip results without title

        # Image
        img = card.select_one("img[src*='amazon']") or card.select_one("img")
        if img:
            result.image_url = (
                img.get("src", "") or img.get("data-src", "")
            ).strip()

        # Price
        for price_sel in [
            ".a-price .a-offscreen",
            ".a-price-whole",
            "[data-a-color='price'] .a-offscreen",
        ]:
            el = card.select_one(price_sel)
            if el:
                result.price = _parse_price(el.get_text(strip=True))
                if result.price:
                    break

        # Rating
        for rating_sel in [".a-icon-alt", "[class*='star']"]:
            el = card.select_one(rating_sel)
            if el:
                result.rating = _parse_rating(el.get("title", "") or el.get_text())
                if result.rating:
                    break

        # Review count
        for count_sel in [".a-size-base[aria-label]", ".a-size-base + span"]:
            el = card.select_one(count_sel)
            if el:
                result.rating_count = _parse_count(
                    el.get("aria-label", "") or el.get_text()
                )
                if result.rating_count:
                    break

        # Badges
        card_text = card.get_text().lower()
        result.is_bestseller    = any(s in card_text for s in ["best seller", "bestseller", "meilleure vente"])
        result.is_amazon_choice = "amazon's choice" in card_text or "choix d'amazon" in card_text
        result.is_prime         = "prime" in card_text

        # Product URL
        link = card.select_one("a[href*='/dp/']")
        if link:
            href = link.get("href", "")
            result.product_url = f"https://www.{marketplace}{href}" if href.startswith("/") else href

        results.append(result)

    return results


# ── Amazon Search Engine ──────────────────────────────────────────────────────

class AmazonSearchEngine:
    """
    Amazon keyword search and product discovery engine.

    Fetches search result pages, extracts product cards,
    applies quality filters, and returns ranked results.

    Usage:
        engine = AmazonSearchEngine(marketplace="amazon.fr")

        # Search by keyword
        results = await engine.search("machine a cafe capsule", max_results=20)

        # Get best sellers
        results = await engine.get_bestsellers(category="kitchen")

        # Filter and rank
        top = engine.filter_and_rank(results, min_rating=4.0, min_reviews=50)
    """

    def __init__(
        self,
        marketplace:   str   = "amazon.fr",
        min_rating:    float = 3.5,
        min_reviews:   int   = 10,
        require_image: bool  = True,
    ):
        self.marketplace   = marketplace
        self.min_rating    = min_rating
        self.min_reviews   = min_reviews
        self.require_image = require_image

    async def search(
        self,
        keyword:     str,
        max_results: int = 20,
        max_pages:   int = 2,
        sort_by:     str = "relevanceblender",
    ) -> list[SearchResult]:
        """
        Search Amazon by keyword.

        Fetches up to max_pages of results, filters, and returns top results.
        Cache TTL: 20 minutes (search results change frequently).
        """
        from scraping.fetch.engine import HybridFetchEngine

        all_results: list[SearchResult] = []
        engine = HybridFetchEngine(content_type="search")

        for page in range(1, max_pages + 1):
            url = build_search_url(keyword, self.marketplace, page, sort_by)
            logger.info(f"[search] Fetching page {page}: {url[:80]}")

            result = await engine.fetch(url)
            if not result.success:
                logger.warning(f"[search] Fetch failed for page {page}")
                break

            page_results = _extract_results_from_html(result.html, self.marketplace)
            if not page_results:
                logger.info(f"[search] No results on page {page} — stopping")
                break

            all_results.extend(page_results)
            logger.info(f"[search] Page {page}: {len(page_results)} results extracted")

            if len(all_results) >= max_results:
                break

        # Filter and rank
        ranked = self.filter_and_rank(all_results)
        logger.info(
            f"[search] '{keyword}': {len(all_results)} found, "
            f"{len(ranked)} passed filters"
        )
        return ranked[:max_results]

    async def get_bestsellers(
        self,
        category_node: str = "",
        max_results:   int = 20,
    ) -> list[SearchResult]:
        """
        Fetch Amazon Best Sellers page.

        Returns products ranked by bestseller position.
        Cache TTL: 1 hour.
        """
        from scraping.fetch.engine import HybridFetchEngine

        url    = build_bestseller_url(category_node, self.marketplace)
        engine = HybridFetchEngine(content_type="bestseller")

        result = await engine.fetch(url)
        if not result.success:
            return []

        results = _extract_results_from_html(result.html, self.marketplace)
        return self.filter_and_rank(results)[:max_results]

    def filter_and_rank(
        self,
        results:     list[SearchResult],
        min_rating:  Optional[float] = None,
        min_reviews: Optional[int]   = None,
    ) -> list[SearchResult]:
        """
        Filter by quality thresholds and rank by quality score.

        Returns sorted list (best first).
        """
        mr  = min_rating  or self.min_rating
        mrv = min_reviews or self.min_reviews

        filtered = [
            r for r in results
            if r.passes_filter(mr, mrv, self.require_image)
        ]
        # Sort by quality score (descending)
        filtered.sort(key=lambda r: r.quality_score, reverse=True)
        # Deduplicate by ASIN
        seen: set[str] = set()
        unique = []
        for r in filtered:
            if r.asin not in seen:
                unique.append(r)
                seen.add(r.asin)
        return unique


# ── Module-level convenience ──────────────────────────────────────────────────

async def search_amazon(
    keyword:     str,
    marketplace: str  = "amazon.fr",
    max_results: int  = 20,
    min_rating:  float = 3.5,
    min_reviews: int  = 10,
) -> list[SearchResult]:
    """
    Convenience function for Amazon keyword search.

    Usage:
        results = await search_amazon("cafetiere dolce gusto", max_results=10)
        for r in results:
            print(r.asin, r.title, r.rating)
    """
    engine = AmazonSearchEngine(
        marketplace=marketplace,
        min_rating=min_rating,
        min_reviews=min_reviews,
    )
    return await engine.search(keyword, max_results=max_results)
