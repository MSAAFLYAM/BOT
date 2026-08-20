"""
scraping/amazon/parser.py — Amazon-specific product data extraction.

Wraps MultiStrategyExtractor with Amazon-specific:
  - CSS selector overrides for Amazon's unique HTML structure
  - Amazon-specific JSON data extraction (imageBlockATF, twister-plus, etc.)
  - Price normalization for different Amazon formats
  - ASIN validation in extracted data
  - Badge extraction (Bestseller, Amazon's Choice, Coupon)

Why not rely only on MultiStrategyExtractor:
  Amazon's product pages include structured JSON-LD (extracted by strategy 1).
  But Amazon ALSO stores data in custom JS objects (imageBlockATF, etc.)
  that require Amazon-specific parsing.
  This parser adds Amazon-specific logic ON TOP of the generic extractor.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# ── Product Data ──────────────────────────────────────────────────────────────

@dataclass
class ProductData:
    """Fully extracted Amazon product data."""
    asin:          str              = ""
    marketplace:   str              = "amazon.fr"
    affiliate_link:str              = ""
    original_url:  str              = ""
    title:         str              = ""
    short_description: str          = ""
    brand:         str              = ""
    category:      str              = ""
    price:         Optional[Decimal] = None
    old_price:     Optional[Decimal] = None
    discount_pct:  Optional[int]    = None
    currency:      str              = "EUR"
    rating:        Optional[float]  = None
    reviews_count: Optional[int]    = None
    is_available:  bool             = True
    is_bestseller: bool             = False
    bestseller_rank: str            = ""
    is_amazon_choice: bool          = False
    coupon_text:   str              = ""
    image_url:     str              = ""
    image_urls:    list             = field(default_factory=list)
    scrape_method: str              = ""

    def is_valid(self) -> bool:
        return bool(self.title and self.asin)

    def passes_quality_filter(
        self,
        min_rating:  float = 3.5,
        min_reviews: int   = 10,
    ) -> tuple[bool, str]:
        if not self.title:
            return False, "No title extracted"
        if not self.is_available:
            return False, "Product not available"
        if self.rating is not None and self.rating < min_rating:
            return False, f"Rating {self.rating} < {min_rating}"
        if self.reviews_count is not None and self.reviews_count < min_reviews:
            return False, f"Reviews {self.reviews_count} < {min_reviews}"
        return True, ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _parse_price(text: str) -> Optional[Decimal]:
    if not text:
        return None
    cleaned = re.sub(r"[€$£¥₹\s]", "", text)
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
    except Exception:
        return None


def _parse_rating(text: str) -> Optional[float]:
    m = re.search(r"(\d+[,.]?\d*)", str(text or ""))
    if m:
        try:
            return round(float(m.group(1).replace(",", ".")), 1)
        except ValueError:
            pass
    return None


def _parse_count(text: str) -> Optional[int]:
    m = re.search(r"[\d,\s]+", str(text or ""))
    if m:
        try:
            return int(re.sub(r"[\s,]", "", m.group()))
        except ValueError:
            pass
    return None


# ── Amazon Parser ─────────────────────────────────────────────────────────────

class AmazonParser:
    """
    Amazon product page parser.

    Orchestrates:
      1. Generic MultiStrategyExtractor (JSON-LD → meta → DOM)
      2. Amazon-specific overrides (Amazon custom JS data)
      3. Image gallery extraction from Amazon JS
      4. Badge extraction

    Usage:
        parser = AmazonParser()
        data = parser.parse(html, asin="B08XYZ123", marketplace="amazon.fr")
    """

    def parse(
        self,
        html:          str,
        asin:          str,
        marketplace:   str = "amazon.fr",
        affiliate_link:str = "",
        original_url:  str = "",
        scrape_method: str = "",
    ) -> Optional[ProductData]:
        """
        Parse Amazon product HTML into ProductData.

        Returns None if extraction completely fails.
        """
        if not html or len(html) < 500:
            return None

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            try:
                soup = BeautifulSoup(html, "html.parser")
            except Exception:
                return None

        # Step 1: Generic extraction
        from scraping.extract.strategies import get_extractor
        generic = get_extractor().extract(html, original_url)

        # Step 2: Build ProductData from generic result
        data = ProductData(
            asin=asin,
            marketplace=marketplace,
            affiliate_link=affiliate_link,
            original_url=original_url,
            scrape_method=scrape_method,
            title=generic.title or "",
            brand=generic.brand or "",
            category=generic.category or "",
            price=generic.price,
            old_price=generic.old_price,
            currency=generic.currency or self._detect_currency(marketplace),
            rating=generic.rating,
            reviews_count=generic.rating_count,
            image_url=generic.image_url or "",
            image_urls=generic.image_urls or [],
            is_available=generic.is_available if generic.is_available is not None else True,
        )

        # Step 3: Amazon-specific overrides
        self._extract_title(soup, data)
        self._extract_price(soup, data)
        self._extract_rating(soup, data)
        self._extract_images(soup, html, data)
        self._extract_brand(soup, data)
        self._extract_availability(soup, data)
        self._extract_description(soup, data)
        self._extract_badges(soup, data)
        self._extract_category(soup, data)

        if not data.title:
            logger.warning(f"[parser] No title for ASIN {asin}")
            return None

        return data

    def _detect_currency(self, marketplace: str) -> str:
        return {
            "amazon.com": "USD", "amazon.co.uk": "GBP",
            "amazon.co.jp": "JPY", "amazon.ca": "CAD",
        }.get(marketplace, "EUR")

    def _extract_title(self, soup: BeautifulSoup, data: ProductData) -> None:
        if data.title:
            return
        for sel in ["#productTitle", "h1#title span", "[data-hook='product-title']"]:
            el = soup.select_one(sel)
            if el:
                t = _clean(el.get_text())
                if t:
                    data.title = t
                    return

    def _extract_price(self, soup: BeautifulSoup, data: ProductData) -> None:
        PRICE_SELS = [
            "#corePriceDisplay_desktop_feature_div .a-offscreen",
            ".apexPriceToPay .a-offscreen",
            ".a-price.aok-align-center .a-offscreen",
            "#priceblock_ourprice", "#priceblock_dealprice",
            ".a-price-whole",
        ]
        if not data.price:
            for sel in PRICE_SELS:
                el = soup.select_one(sel)
                if el:
                    p = _parse_price(el.get_text(strip=True))
                    if p and p > 0:
                        data.price = p
                        break

        OLD_SELS = [
            ".a-text-price .a-offscreen",
            "#priceblock_was_price",
            ".priceBlockStrikePriceString",
        ]
        if not data.old_price:
            for sel in OLD_SELS:
                el = soup.select_one(sel)
                if el:
                    op = _parse_price(el.get_text(strip=True))
                    if op and data.price and op > data.price:
                        data.old_price   = op
                        data.discount_pct = int((1 - data.price / op) * 100)
                        break

    def _extract_rating(self, soup: BeautifulSoup, data: ProductData) -> None:
        if not data.rating:
            for sel in ["[data-hook='rating-out-of-text']", "#acrPopover .a-icon-alt"]:
                el = soup.select_one(sel)
                if el:
                    r = _parse_rating(el.get("title", "") or el.get_text())
                    if r and 0 < r <= 5:
                        data.rating = r
                        break

        if not data.reviews_count:
            for sel in ["[data-hook='total-review-count']", "#acrCustomerReviewText"]:
                el = soup.select_one(sel)
                if el:
                    c = _parse_count(el.get_text(strip=True))
                    if c is not None:
                        data.reviews_count = c
                        break

    def _extract_images(self, soup: BeautifulSoup, html: str, data: ProductData) -> None:
        if not data.image_url:
            for sel in ["#imgBlkFront", "#landingImage", "#main-image"]:
                el = soup.select_one(sel)
                if el:
                    src = (el.get("data-old-hires") or el.get("data-a-hires")
                           or el.get("src", ""))
                    if src and src.startswith("http") and "base64" not in src:
                        data.image_url = src
                        break

        # Gallery from JS
        if len(data.image_urls) < 2:
            matches = re.findall(r'"hiRes"\s*:\s*"(https://[^"]+)"', html)
            for url in matches[:5]:
                if url not in data.image_urls:
                    data.image_urls.append(url)
            if data.image_urls and not data.image_url:
                data.image_url = data.image_urls[0]

    def _extract_brand(self, soup: BeautifulSoup, data: ProductData) -> None:
        if data.brand:
            return
        for sel in ["#bylineInfo", "#brand", ".po-brand .po-break-word"]:
            el = soup.select_one(sel)
            if el:
                t = re.sub(r"^(Marque|Brand|Visit|Visiter)[\s:]*", "",
                           _clean(el.get_text()), flags=re.I)
                if t:
                    data.brand = t
                    return

    def _extract_availability(self, soup: BeautifulSoup, data: ProductData) -> None:
        OUT_SIGNALS = ["indisponible", "rupture", "out of stock",
                       "currently unavailable", "non disponible"]
        el = soup.select_one("#availability span")
        if el:
            text = el.get_text(strip=True).lower()
            data.is_available = not any(s in text for s in OUT_SIGNALS)

    def _extract_description(self, soup: BeautifulSoup, data: ProductData) -> None:
        bullets = []
        for sel in ["#feature-bullets ul li", "#feature-bullets .a-list-item"]:
            for el in soup.select(sel)[:6]:
                t = _clean(el.get_text())
                if len(t) > 5:
                    bullets.append(f"• {t}")
        if bullets:
            data.short_description = "\n".join(bullets[:6])

    def _extract_badges(self, soup: BeautifulSoup, data: ProductData) -> None:
        text = soup.get_text(" ").lower()
        data.is_bestseller     = any(s in text for s in
                                     ["n°1", "#1 best", "bestseller", "meilleure vente"])
        data.is_amazon_choice  = "choix d'amazon" in text or "amazon's choice" in text

        for sel in ["#couponFeature .a-color-success", "[data-hook='coupon-title']"]:
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                data.coupon_text = el.get_text(strip=True)
                break

        # Bestseller rank
        rank_el = soup.select_one("#SalesRank")
        if rank_el:
            m = re.search(r"(n°?\s*\d+|#\d+)", rank_el.get_text(), re.I)
            if m:
                data.bestseller_rank = m.group()[:100]

    def _extract_category(self, soup: BeautifulSoup, data: ProductData) -> None:
        if data.category:
            return
        for sel in [
            "#wayfinding-breadcrumbs_feature_div li:last-child a",
            ".a-breadcrumb li:last-child a",
        ]:
            el = soup.select_one(sel)
            if el:
                t = _clean(el.get_text())
                if t and len(t) > 2:
                    data.category = t
                    return
