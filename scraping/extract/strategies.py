"""
scraping/extract/strategies.py — Multi-strategy content extraction.

Spec requirement: DO NOT rely only on DOM selectors.

Extraction priority (most reliable → least reliable):
  1. JSON-LD / embedded JSON    → Schema.org structured data (most sites include this)
  2. Microdata (HTML)           → itemscope/itemprop attributes
  3. Open Graph / Meta tags     → og:title, og:description, og:image
  4. DOM / CSS selectors        → fallback for sites without structured data

Why this order:
  - JSON-LD is site-agnostic: same extraction code works for 1000+ sites.
  - DOM selectors are fragile: Amazon changes IDs/classes frequently.
  - JSON-LD rarely changes: it's for search engines (SEO critical).

Architecture:
  - Each strategy returns ExtractedData (partial — may have some None fields).
  - MultiStrategyExtractor merges results (later strategies fill gaps).
  - If strategy 1 gets title but no price → strategy 4 fills price.
  - This makes extraction more resilient to partial data availability.

Schema evolution:
  - Site structures change → DOM selectors break.
  - JSON-LD specifications are stable (Schema.org versioning).
  - New strategies can be added without changing the orchestrator.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


# ── Extracted Data ─────────────────────────────────────────────────────────────

@dataclass
class ExtractedData:
    """
    Data extracted from a web page.
    Fields may be None if a strategy didn't find them.
    """
    # Content
    title:        Optional[str]         = None
    description:  Optional[str]         = None
    image_url:    Optional[str]         = None
    image_urls:   list[str]             = field(default_factory=list)

    # Pricing (for products)
    price:        Optional[Decimal]     = None
    old_price:    Optional[Decimal]     = None
    currency:     Optional[str]         = None
    discount_pct: Optional[int]         = None

    # Ratings (for products)
    rating:       Optional[float]       = None
    rating_count: Optional[int]         = None



    # Product-specific
    brand:        Optional[str]         = None
    is_available: Optional[bool]        = None

    # Metadata
    url:          Optional[str]         = None
    source_type:  str                   = ""  # "product" | "article" | "unknown"
    strategy:     str                   = ""  # Which strategy extracted this

    def is_empty(self) -> bool:
        return not (self.title or self.description or self.image_url)

    def merge(self, other: "ExtractedData") -> "ExtractedData":
        """Merge another result into this one, filling None fields."""
        for attr in vars(self):
            if attr in ("strategy", "source_type"):
                continue
            if getattr(self, attr) is None and getattr(other, attr) is not None:
                setattr(self, attr, getattr(other, attr))
            elif attr == "image_urls" and not self.image_urls and other.image_urls:
                self.image_urls = other.image_urls
        return self


# ── ISO duration parser ───────────────────────────────────────────────────────

def _parse_duration(iso: str) -> Optional[str]:
    """PT30M → '30 min', PT1H30M → '1h 30min'"""
    if not iso:
        return None
    try:
        h = re.search(r"(\d+)H", iso)
        m = re.search(r"(\d+)M", iso)
        parts = []
        if h:
            parts.append(f"{h.group(1)}h")
        if m:
            parts.append(f"{m.group(1)}min")
        return " ".join(parts) if parts else iso
    except Exception:
        return iso


def _parse_price(text: str) -> Optional[Decimal]:
    """Parse price string: '3,99 €' → Decimal('3.99')"""
    if not text:
        return None
    cleaned = re.sub(r"[€$£¥₹\s]", "", text.strip())
    cleaned = re.sub(r"[^\d,.]", "", cleaned)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rindex(",") > cleaned.rindex("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _clean_text(text: str) -> str:
    """Remove excessive whitespace from extracted text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


# ── Base Strategy ─────────────────────────────────────────────────────────────

class ExtractionStrategy(ABC):
    """Abstract base for extraction strategies."""

    @abstractmethod
    def extract(self, soup: BeautifulSoup, url: str = "") -> ExtractedData:
        """
        Extract data from BeautifulSoup document.

        Returns ExtractedData — fields may be None.
        Never raises exceptions — returns empty ExtractedData on failure.
        """
        ...

    def _safe_extract(self, soup: BeautifulSoup, url: str) -> ExtractedData:
        """Wrapper that catches all exceptions."""
        try:
            return self.extract(soup, url)
        except Exception as e:
            logger.debug(f"{type(self).__name__} failed: {e}")
            return ExtractedData()


# ── Strategy 1: JSON-LD ───────────────────────────────────────────────────────

class JsonLdStrategy(ExtractionStrategy):
    """
    Extract from JSON-LD structured data (Schema.org).

    Most modern sites include this for SEO.
    Works for: Product, Article, BreadcrumbList.

    Priority: HIGHEST — most reliable, schema is stable.
    """

    def extract(self, soup: BeautifulSoup, url: str = "") -> ExtractedData:
        data = ExtractedData(strategy="json-ld", url=url)

        # Find all JSON-LD scripts
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                raw = script.string or script.get_text()
                if not raw:
                    continue
                obj = json.loads(raw)

                # Handle @graph arrays
                objects = []
                if isinstance(obj, dict):
                    if "@graph" in obj:
                        objects = obj["@graph"]
                    else:
                        objects = [obj]
                elif isinstance(obj, list):
                    objects = obj

                for item in objects:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("@type", ""))

                    if "Product" in item_type:
                        self._extract_product(item, data)
                    elif "Article" in item_type or "NewsArticle" in item_type:
                        self._extract_article(item, data)

            except (json.JSONDecodeError, Exception):
                continue

        return data

    def _extract_product(self, item: dict, data: ExtractedData) -> None:
        data.source_type = "product"
        if not data.title:
            data.title = _clean_text(str(item.get("name", "")))
        if not data.description:
            data.description = _clean_text(str(item.get("description", "")))
        if not data.brand:
            brand = item.get("brand", {})
            if isinstance(brand, dict):
                data.brand = _clean_text(str(brand.get("name", "")))
            elif isinstance(brand, str):
                data.brand = brand

        # Image
        img = item.get("image", "")
        if isinstance(img, list):
            img = img[0] if img else ""
        if isinstance(img, dict):
            img = img.get("url", "")
        if img and not data.image_url:
            data.image_url = str(img)

        # Price from offers
        offers = item.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            price_str = str(offers.get("price", ""))
            p = _parse_price(price_str)
            if p and not data.price:
                data.price = p
            currency = offers.get("priceCurrency", "")
            if currency and not data.currency:
                data.currency = currency
            avail = str(offers.get("availability", "")).lower()
            if avail and data.is_available is None:
                data.is_available = "instock" in avail or "inStock" in avail

        # Rating
        agg = item.get("aggregateRating", {})
        if isinstance(agg, dict):
            rv = agg.get("ratingValue")
            rc = agg.get("ratingCount") or agg.get("reviewCount")
            if rv is not None and data.rating is None:
                try:
                    data.rating = round(float(str(rv).replace(",", ".")), 1)
                except (ValueError, TypeError):
                    pass
            if rc is not None and data.rating_count is None:
                try:
                    data.rating_count = int(str(rc).replace(",", "").replace(".", ""))
                except (ValueError, TypeError):
                    pass

    def _extract_article(self, item: dict, data: ExtractedData) -> None:
        data.source_type = "article"
        if not data.title:
            data.title = _clean_text(str(item.get("name") or item.get("headline", "")))
        if not data.description:
            data.description = _clean_text(str(item.get("description", "")))
        img = item.get("image", {})
        if isinstance(img, dict) and not data.image_url:
            data.image_url = img.get("url", "")


# ── Strategy 2: Open Graph / Meta Tags ────────────────────────────────────────

class MetaTagStrategy(ExtractionStrategy):
    """
    Extract from Open Graph and standard meta tags.

    Works for virtually ALL websites (og:title, og:image are universal).
    Less specific than JSON-LD (no prices, no ingredients).

    Used to fill: title, description, image_url.
    """

    def extract(self, soup: BeautifulSoup, url: str = "") -> ExtractedData:
        data = ExtractedData(strategy="meta-tags", url=url)

        # Open Graph
        og = {
            m.get("property", ""): m.get("content", "")
            for m in soup.find_all("meta", property=True)
            if m.get("content")
        }

        if og.get("og:title") and not data.title:
            data.title = _clean_text(og["og:title"])
        if og.get("og:description") and not data.description:
            data.description = _clean_text(og["og:description"])
        if og.get("og:image") and not data.image_url:
            data.image_url = og["og:image"]
        if og.get("og:type"):
            t = og["og:type"].lower()
            if "product" in t:
                data.source_type = "product"

        # Twitter card (fallback for image)
        twitter = {
            m.get("name", ""): m.get("content", "")
            for m in soup.find_all("meta", attrs={"name": True})
            if m.get("content")
        }
        if twitter.get("twitter:image") and not data.image_url:
            data.image_url = twitter["twitter:image"]
        if twitter.get("twitter:title") and not data.title:
            data.title = _clean_text(twitter["twitter:title"])

        # Standard description meta
        desc_meta = soup.find("meta", attrs={"name": "description"})
        if desc_meta and desc_meta.get("content") and not data.description:
            data.description = _clean_text(desc_meta["content"])

        # Page title fallback
        if not data.title:
            title_tag = soup.find("title")
            if title_tag:
                data.title = _clean_text(title_tag.get_text())

        return data


# ── Strategy 3: DOM / CSS selectors ──────────────────────────────────────────

class DOMStrategy(ExtractionStrategy):
    """
    DOM-based extraction using CSS selectors.

    Fallback when JSON-LD and meta tags don't provide enough data.
    Uses multiple selector candidates per field (resilience to site changes).

    NOTE: This is the most fragile strategy — site structure changes
    may break selectors. The HTMLCache snapshot system allows offline
    debugging when this happens.
    """

    def extract(self, soup: BeautifulSoup, url: str = "") -> ExtractedData:
        data = ExtractedData(strategy="dom", url=url)

        if not data.title:
            data.title = self._first_text(soup, [
                "h1", ".title", ".product-title",
                "[class*='title']", "[class*='name']",
            ])

        if not data.image_url:
            for sel in ["img[class*='main']", "img[id*='main']", "img.hero",
                        ".product-image img", "article img"]:
                el = soup.select_one(sel)
                if el:
                    src = el.get("src") or el.get("data-src") or ""
                    if src and src.startswith("http"):
                        data.image_url = src
                        break

        if not data.description:
            data.description = self._first_text(soup, [
                "[class*='description']", "[class*='summary']",
                ".intro", ".lead", "article > p:first-of-type",
            ])

        return data

    def _first_text(self, soup: BeautifulSoup, selectors: list) -> Optional[str]:
        for sel in selectors:
            try:
                el = soup.select_one(sel)
                if el:
                    text = _clean_text(el.get_text())
                    if text and len(text) > 2:
                        return text
            except Exception:
                continue
        return None


# ── Multi-Strategy Orchestrator ───────────────────────────────────────────────

class MultiStrategyExtractor:
    """
    Orchestrates all extraction strategies.

    Runs strategies in priority order.
    Merges results: later strategies fill gaps from earlier ones.

    Usage:
        extractor = MultiStrategyExtractor()
        data = extractor.extract(html, url)

        if data.source_type == "product":
            # use data.price, data.rating, etc.
    """

    def __init__(self):
        self._strategies = [
            JsonLdStrategy(),
            MetaTagStrategy(),
            DOMStrategy(),
        ]

    def extract(self, html: str, url: str = "") -> ExtractedData:
        """
        Run all strategies and merge results.

        Args:
            html: Raw HTML string.
            url:  Source URL (used for context and metadata).

        Returns:
            Merged ExtractedData from all strategies.
        """
        if not html:
            return ExtractedData(url=url)

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            try:
                soup = BeautifulSoup(html, "html.parser")
            except Exception:
                return ExtractedData(url=url)

        merged = ExtractedData(url=url)

        for strategy in self._strategies:
            result = strategy._safe_extract(soup, url)
            if not result.is_empty():
                merged = merged.merge(result)
                if not merged.strategy:
                    merged.strategy = result.strategy

        logger.debug(
            f"Extraction: strategy={merged.strategy}, "
            f"type={merged.source_type}, "
            f"title={bool(merged.title)}, "
            f"image={bool(merged.image_url)}"
        )
        return merged


# ── Module singleton ───────────────────────────────────────────────────────────

_extractor: Optional[MultiStrategyExtractor] = None


def get_extractor() -> MultiStrategyExtractor:
    """Return module-level extractor singleton."""
    global _extractor
    if _extractor is None:
        _extractor = MultiStrategyExtractor()
    return _extractor
