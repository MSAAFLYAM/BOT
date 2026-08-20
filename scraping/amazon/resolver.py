"""
scraping/amazon/resolver.py — Amazon URL normalization and ASIN extraction.

Architecture decisions:
  - Handles ALL Amazon URL formats (canonical, shortened, tracking, affiliate).
  - amzn.to and a.co short URLs are resolved by following redirects.
  - ASIN is extracted with regex patterns in priority order.
  - Marketplace is detected from domain (amazon.fr, amazon.com, etc.).
  - Canonical URL is constructed from ASIN + marketplace for consistency.
    This prevents scraping the same product twice with different URL formats.
  - Async resolution (httpx) for shortened URLs — non-blocking.

Supported URL formats:
  - https://www.amazon.fr/dp/B08XYZ123
  - https://www.amazon.fr/gp/product/B08XYZ123
  - https://www.amazon.fr/Product-Name/dp/B08XYZ123/ref=...
  - https://www.amazon.fr/s?k=keywords (search — returns None)
  - https://amzn.to/3xYZABC  → follow redirect → extract ASIN
  - https://a.co/d/B08XYZ123 → follow redirect → extract ASIN
  - B08XYZ123 (bare ASIN) → construct canonical URL
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

logger = logging.getLogger(__name__)

# ── ASIN patterns (in priority order) ────────────────────────────────────────

_ASIN_PATTERNS = [
    re.compile(r"/dp/([A-Z0-9]{10})(?:/|\?|$)"),
    re.compile(r"/gp/product/([A-Z0-9]{10})(?:/|\?|$)"),
    re.compile(r"/ASIN/([A-Z0-9]{10})(?:/|\?|$)"),
    re.compile(r"/product/([A-Z0-9]{10})(?:/|\?|$)"),
    re.compile(r"/exec/obidos/ASIN/([A-Z0-9]{10})"),
    re.compile(r"(?:^|/)([A-Z0-9]{10})(?:/|\?|$)"),  # bare ASIN in path
]

_BARE_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

# ── Marketplace configuration ────────────────────────────────────────────────

_MARKETPLACE_MAP = {
    "amazon.fr":     {"code": "FR", "currency": "EUR", "lang": "fr"},
    "amazon.com":    {"code": "US", "currency": "USD", "lang": "en"},
    "amazon.co.uk":  {"code": "GB", "currency": "GBP", "lang": "en"},
    "amazon.de":     {"code": "DE", "currency": "EUR", "lang": "de"},
    "amazon.es":     {"code": "ES", "currency": "EUR", "lang": "es"},
    "amazon.it":     {"code": "IT", "currency": "EUR", "lang": "it"},
    "amazon.ca":     {"code": "CA", "currency": "CAD", "lang": "en"},
    "amazon.com.mx": {"code": "MX", "currency": "MXN", "lang": "es"},
    "amazon.com.br": {"code": "BR", "currency": "BRL", "lang": "pt"},
    "amazon.co.jp":  {"code": "JP", "currency": "JPY", "lang": "ja"},
    "amazon.in":     {"code": "IN", "currency": "INR", "lang": "en"},
    "amazon.com.au": {"code": "AU", "currency": "AUD", "lang": "en"},
    "amazon.ae":     {"code": "AE", "currency": "AED", "lang": "ar"},
    "amazon.sa":     {"code": "SA", "currency": "SAR", "lang": "ar"},
    "amazon.eg":     {"code": "EG", "currency": "EGP", "lang": "ar"},
}

_SHORT_DOMAINS = {"amzn.to", "amzn.eu", "a.co"}


@dataclass
class AmazonURL:
    """
    Resolved Amazon URL with extracted metadata.

    All fields are populated after resolution.
    canonical_url is the clean URL for scraping (no tracking params).
    """
    asin:          str
    marketplace:   str              = "amazon.fr"
    canonical_url: str              = ""
    country_code:  str              = "FR"
    currency:      str              = "EUR"
    language:      str              = "fr"
    original_url:  str              = ""
    affiliate_tag: str              = ""
    was_shortened: bool             = False

    def __post_init__(self):
        if not self.canonical_url:
            self.canonical_url = f"https://www.{self.marketplace}/dp/{self.asin}"

    @property
    def product_page_url(self) -> str:
        """Clean product URL without tracking parameters."""
        return f"https://www.{self.marketplace}/dp/{self.asin}"

    @property
    def affiliate_url(self) -> str:
        """Product URL with affiliate tag."""
        tag = self.affiliate_tag
        if not tag:
            import os
            tag = os.environ.get("AFFILIATE_TAG", "")
        if tag:
            return f"https://www.{self.marketplace}/dp/{self.asin}?tag={tag}"
        return self.product_page_url


def extract_asin(url: str) -> Optional[str]:
    """
    Extract ASIN from an Amazon URL.

    Returns the 10-character ASIN string or None if not found.

    Works on:
      - Full URLs with /dp/ path
      - Bare ASIN strings
      - URLs with various tracking parameters
    """
    if not url:
        return None

    # Bare ASIN (10 uppercase alphanumeric chars)
    url = url.strip()
    if _BARE_ASIN_RE.match(url):
        return url

    # Try each pattern
    for pattern in _ASIN_PATTERNS:
        match = pattern.search(url)
        if match:
            asin = match.group(1)
            if len(asin) == 10:
                return asin

    return None


def detect_marketplace(url: str) -> tuple[str, dict]:
    """
    Detect Amazon marketplace from URL.

    Returns:
        (marketplace_domain, marketplace_info_dict)
        e.g. ("amazon.fr", {"code": "FR", "currency": "EUR", "lang": "fr"})
    """
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().lstrip("www.")
        if netloc in _MARKETPLACE_MAP:
            return netloc, _MARKETPLACE_MAP[netloc]
    except Exception:
        pass
    # Default to amazon.fr
    return "amazon.fr", _MARKETPLACE_MAP["amazon.fr"]


def is_amazon_url(url: str) -> bool:
    """Return True if the URL is from any Amazon domain or shortlink."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().lstrip("www.")
        return (
            any(netloc == d or netloc.endswith("." + d) for d in _MARKETPLACE_MAP)
            or netloc in _SHORT_DOMAINS
            or bool(_BARE_ASIN_RE.match(url.strip()))
        )
    except Exception:
        return False


def is_short_url(url: str) -> bool:
    """Return True if this is a shortened Amazon URL (amzn.to, a.co)."""
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc in _SHORT_DOMAINS
    except Exception:
        return False


async def resolve_short_url(url: str, timeout: int = 15) -> str:
    """
    Resolve a shortened Amazon URL to its full product URL.

    amzn.to/3xYZABC → https://www.amazon.fr/dp/B08XYZ123/...

    Uses HTTPX to follow redirects without downloading the full page.
    HEAD request is tried first (no body), falls back to GET.
    """
    try:
        import httpx
        from scraping.headers import get_headers
        headers = get_headers(url, desktop_only=True)

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            headers=headers,
        ) as client:
            # Try HEAD first (faster, no body download)
            try:
                resp = await client.head(url)
                resolved = str(resp.url)
                if "/dp/" in resolved or "/gp/product/" in resolved:
                    return resolved
            except Exception:
                pass

            # Fall back to GET
            resp = await client.get(url)
            return str(resp.url)

    except Exception as e:
        logger.warning(f"Short URL resolution failed for {url}: {e}")
        return url


def clean_amazon_url(url: str) -> str:
    """
    Remove tracking/affiliate parameters from Amazon URL.

    Keeps only: /dp/ASIN
    Removes: ref=, tag=, linkCode=, linkId=, etc.

    Used to generate a canonical URL for cache key.
    """
    try:
        parsed = urlparse(url)
        marketplace, info = detect_marketplace(url)
        asin = extract_asin(url)
        if asin:
            return f"https://www.{marketplace}/dp/{asin}"
    except Exception:
        pass
    return url


async def resolve_amazon_url(
    url: str,
    affiliate_tag: str = "",
) -> Optional[AmazonURL]:
    """
    Full URL resolution pipeline.

    1. Detect if shortened URL → resolve redirect
    2. Extract ASIN
    3. Detect marketplace
    4. Build canonical URL
    5. Return AmazonURL dataclass

    Args:
        url:           Raw URL (any format)
        affiliate_tag: Optional affiliate tag to embed in result

    Returns:
        AmazonURL if ASIN was found, None otherwise.

    Usage:
        amazon_url = await resolve_amazon_url("amzn.to/3xYZABC")
        if amazon_url:
            print(amazon_url.asin)         # "B08XYZ123"
            print(amazon_url.affiliate_url) # "https://www.amazon.fr/dp/B08XYZ123?tag=xxx"
    """
    if not url:
        return None

    original = url.strip()
    was_shortened = False

    # Handle bare ASIN
    if _BARE_ASIN_RE.match(original):
        return AmazonURL(
            asin=original,
            marketplace="amazon.fr",
            affiliate_tag=affiliate_tag,
            original_url=original,
        )

    # Resolve short URLs
    if is_short_url(url):
        logger.debug(f"Resolving short URL: {url}")
        url = await resolve_short_url(url)
        was_shortened = True
        logger.debug(f"Resolved to: {url}")

    # Extract ASIN
    asin = extract_asin(url)
    if not asin:
        from core.exceptions import ASINNotFoundError
        logger.warning(f"No ASIN found in URL: {url[:100]}")
        return None

    # Detect marketplace
    marketplace, info = detect_marketplace(url)

    # Extract affiliate tag from URL if present
    if not affiliate_tag:
        try:
            params = parse_qs(urlparse(url).query)
            affiliate_tag = params.get("tag", [""])[0]
        except Exception:
            pass

    return AmazonURL(
        asin=asin,
        marketplace=marketplace,
        canonical_url=f"https://www.{marketplace}/dp/{asin}",
        country_code=info["code"],
        currency=info["currency"],
        language=info["lang"],
        original_url=original,
        affiliate_tag=affiliate_tag,
        was_shortened=was_shortened,
    )
