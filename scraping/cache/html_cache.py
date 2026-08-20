"""
scraping/cache/html_cache.py — Production HTML cache with compression and snapshots.

Architecture decisions:
  - zlib compression reduces HTML storage by 60-80% (typical HTML compresses well).
    Amazon product page ~150KB → ~25KB compressed.
    100 cached pages = ~2.5MB Redis memory vs ~15MB uncompressed.
  - Dynamic TTL by content type (product, search, bestseller).
  - HTML snapshot versioning: store failed parser snapshots for debugging.
    When a parser fails, the raw HTML is saved with a timestamp.
    This allows offline debugging of site structure changes.
  - Cache key = SHA-256(normalized_url) to handle URL variants.
  - Separate cache for parsed data (JSON) vs raw HTML.

Cache namespaces:
  html:page:{hash}         → compressed HTML, TTL varies
  html:snapshot:{hash}:{ts} → debug snapshots (parse failures)
  html:parsed:{hash}       → parsed product JSON
  html:search:{hash}       → search results
  html:selector:{hash}     → selector cache

Redis memory estimate:
  100 product pages × 25KB avg = 2.5MB
  50 search pages × 10KB avg = 0.5MB
  Total: ~3MB for typical workload
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import zlib
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs, quote

logger = logging.getLogger(__name__)

# ── TTL Constants ──────────────────────────────────────────────────────────────
TTL_PRODUCT    = 21600   # 6 hours   — product pages (prices change)
TTL_SEARCH     = 1200    # 20 min    — search results (fast-changing)
TTL_BESTSELLER = 3600    # 1 hour    — bestseller pages
TTL_SNAPSHOT   = 604800  # 7 days    — debug snapshots

# ── Key prefixes ───────────────────────────────────────────────────────────────
PFX_PAGE      = "html:page:"
PFX_SNAPSHOT  = "html:snapshot:"
PFX_PARSED    = "html:parsed:"
PFX_SEARCH    = "html:search:"
PFX_SELECTOR  = "html:selector:"


# ── URL Normalization ─────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    """
    Normalize URL for consistent cache keys.

    Removes tracking params (ref=, tag=, linkCode=, etc.)
    that don't affect page content.
    Keeps product-identifying params only.
    """
    STRIP_PARAMS = {
        "ref", "tag", "linkCode", "linkId", "camp", "creative",
        "creativeASIN", "ascsubtag", "th", "psc", "smid",
        "utm_source", "utm_medium", "utm_campaign", "utm_content",
        "pd_rd_r", "pd_rd_w", "pd_rd_wg", "pf_rd_r", "pf_rd_p",
    }
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=False)
        clean_params = {k: v for k, v in params.items() if k not in STRIP_PARAMS}
        clean_query = urlencode(clean_params, doseq=True)
        return urlunparse(parsed._replace(query=clean_query, fragment=""))
    except Exception:
        return url


def _cache_key(url: str) -> str:
    """
    Generate a stable 32-char cache key from a normalized URL.
    SHA-256 is used for collision resistance.
    """
    normalized = _normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


# ── Compression helpers ────────────────────────────────────────────────────────

def _compress(data: str) -> bytes:
    """Compress string to bytes using zlib (level 6 = good balance)."""
    return zlib.compress(data.encode("utf-8"), level=6)


def _decompress(data: bytes) -> str:
    """Decompress zlib bytes to string."""
    return zlib.decompress(data).decode("utf-8")


# ── Cache Result ─────────────────────────────────────────────────────────────

@dataclass
class CacheResult:
    """Result from a cache lookup."""
    hit:       bool
    html:      str              = ""
    from_key:  str              = ""
    age_s:     Optional[float]  = None  # seconds since cached
    compressed_size: int        = 0
    original_size:   int        = 0

    @property
    def compression_ratio(self) -> float:
        if self.original_size == 0:
            return 0.0
        return 1.0 - (self.compressed_size / self.original_size)


# ── HTML Cache ────────────────────────────────────────────────────────────────

class HTMLCache:
    """
    Production HTML cache backed by Redis.

    Features:
      - zlib compression (60-80% space saving)
      - Dynamic TTL by content type
      - Debug snapshot versioning
      - Hit/miss tracking for metrics
      - Separate storage for parsed data

    Usage:
        cache = HTMLCache()
        result = await cache.get(url)
        if result.hit:
            html = result.html
        else:
            html = await scrape(url)
            await cache.set(url, html, content_type="product")
    """

    def __init__(self, compress: bool = True):
        self._compress = compress

    def _get_redis(self):
        from core.redis_client import get_redis
        return get_redis()

    # ── Page cache ────────────────────────────────────────────────────────────

    async def get(self, url: str) -> CacheResult:
        """
        Look up HTML for a URL.

        Returns CacheResult with hit=True and html set if found.
        Returns CacheResult with hit=False if not in cache.
        """
        key = f"{PFX_PAGE}{_cache_key(url)}"
        try:
            redis = self._get_redis()
            data = await redis.get(key)
            if data is None:
                return CacheResult(hit=False, from_key=key)

            # Data is stored as bytes (compressed) or string
            if isinstance(data, bytes) and self._compress:
                try:
                    html = _decompress(data)
                except zlib.error:
                    html = data.decode("utf-8", errors="replace")
            else:
                html = data if isinstance(data, str) else data.decode("utf-8")

            ttl = await redis.ttl(key)

            logger.debug(f"Cache HIT: {url[:60]} (key={key[:16]}...)")
            return CacheResult(
                hit=True,
                html=html,
                from_key=key,
                compressed_size=len(data),
                original_size=len(html),
            )

        except Exception as e:
            logger.warning(f"Cache GET error for {url[:60]}: {e}")
            return CacheResult(hit=False)

    async def set(
        self,
        url:          str,
        html:         str,
        content_type: str = "product",
        ttl:          Optional[int] = None,
    ) -> bool:
        """
        Store HTML in cache with appropriate TTL.

        content_type: "product" | "search" | "bestseller"
        ttl: override TTL in seconds (uses content_type default if None)

        Returns True if stored, False on error.
        """
        if not html or len(html) < 500:
            return False

        # Determine TTL
        if ttl is None:
            ttl = {
                "product":    TTL_PRODUCT,
                "search":     TTL_SEARCH,
                "bestseller": TTL_BESTSELLER,
            }.get(content_type, TTL_PRODUCT)

        key = f"{PFX_PAGE}{_cache_key(url)}"
        try:
            redis = self._get_redis()

            if self._compress:
                data = _compress(html)
                await redis.setex(key, ttl, data)
            else:
                await redis.setex(key, ttl, html.encode("utf-8"))

            comp_size = len(data) if self._compress else len(html)
            ratio = round(1 - comp_size / len(html), 2) if self._compress else 0
            logger.debug(
                f"Cache SET: {url[:60]} "
                f"({len(html):,} → {comp_size:,} bytes, "
                f"{ratio:.0%} compression, TTL={ttl}s)"
            )
            return True

        except Exception as e:
            logger.warning(f"Cache SET error for {url[:60]}: {e}")
            return False

    async def delete(self, url: str) -> bool:
        """Invalidate cache for a URL."""
        key = f"{PFX_PAGE}{_cache_key(url)}"
        try:
            redis = self._get_redis()
            deleted = await redis.delete(key)
            return bool(deleted)
        except Exception as e:
            logger.warning(f"Cache DELETE error: {e}")
            return False

    async def exists(self, url: str) -> bool:
        """Check if URL is in cache without fetching the HTML."""
        key = f"{PFX_PAGE}{_cache_key(url)}"
        try:
            redis = self._get_redis()
            return bool(await redis.exists(key))
        except Exception:
            return False

    # ── Snapshot versioning (debug) ───────────────────────────────────────────

    async def save_snapshot(
        self,
        url:    str,
        html:   str,
        reason: str = "parse_failure",
    ) -> str:
        """
        Save a debug snapshot of HTML with timestamp.

        Used when parser fails to extract data.
        Snapshot key: html:snapshot:{url_hash}:{timestamp}

        Stored for 7 days for offline debugging.
        Returns the snapshot key.
        """
        ts  = int(time.time())
        key = f"{PFX_SNAPSHOT}{_cache_key(url)}:{ts}"
        metadata = json.dumps({
            "url":    url,
            "reason": reason,
            "ts":     ts,
            "size":   len(html),
        })
        try:
            redis = self._get_redis()
            # Store metadata + compressed HTML
            payload = metadata + "\n---HTML---\n" + html
            if self._compress:
                await redis.setex(key, TTL_SNAPSHOT, _compress(payload))
            else:
                await redis.setex(key, TTL_SNAPSHOT, payload.encode("utf-8"))
            logger.info(f"Snapshot saved: {key} (reason={reason}, size={len(html):,})")
            return key
        except Exception as e:
            logger.warning(f"Snapshot save failed: {e}")
            return ""

    async def get_snapshot(self, snapshot_key: str) -> Optional[tuple[dict, str]]:
        """
        Retrieve a debug snapshot.

        Returns (metadata_dict, html) or None.
        """
        try:
            redis = self._get_redis()
            data = await redis.get(snapshot_key)
            if not data:
                return None
            if isinstance(data, bytes):
                try:
                    payload = _decompress(data)
                except zlib.error:
                    payload = data.decode("utf-8")
            else:
                payload = data
            parts = payload.split("\n---HTML---\n", 1)
            if len(parts) != 2:
                return None
            metadata = json.loads(parts[0])
            return metadata, parts[1]
        except Exception as e:
            logger.warning(f"Snapshot get failed: {e}")
            return None

    async def list_snapshots(self, url: str) -> list[str]:
        """List all snapshot keys for a URL."""
        try:
            redis = self._get_redis()
            pattern = f"{PFX_SNAPSHOT}{_cache_key(url)}:*"
            keys = await redis.keys(pattern)
            return sorted(keys)
        except Exception:
            return []

    # ── Parsed data cache ─────────────────────────────────────────────────────

    async def get_parsed(self, url: str) -> Optional[dict]:
        """Retrieve previously parsed product data."""
        key = f"{PFX_PARSED}{_cache_key(url)}"
        try:
            redis = self._get_redis()
            data = await redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"Parsed cache GET error: {e}")
        return None

    async def set_parsed(self, url: str, data: dict, ttl: int = TTL_PRODUCT) -> bool:
        """Store parsed product data."""
        key = f"{PFX_PARSED}{_cache_key(url)}"
        try:
            redis = self._get_redis()
            await redis.setex(key, ttl, json.dumps(data, default=str))
            return True
        except Exception as e:
            logger.warning(f"Parsed cache SET error: {e}")
            return False

    # ── Stats ─────────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        """Return cache statistics from Redis."""
        try:
            redis = self._get_redis()
            page_keys    = await redis.keys(f"{PFX_PAGE}*")
            snapshot_keys= await redis.keys(f"{PFX_SNAPSHOT}*")
            parsed_keys  = await redis.keys(f"{PFX_PARSED}*")
            return {
                "page_entries":     len(page_keys),
                "snapshot_entries": len(snapshot_keys),
                "parsed_entries":   len(parsed_keys),
            }
        except Exception as e:
            return {"error": str(e)}


# ── Module-level singleton ─────────────────────────────────────────────────────

_cache: Optional[HTMLCache] = None


def get_html_cache() -> HTMLCache:
    """Return module-level HTMLCache singleton."""
    global _cache
    if _cache is None:
        _cache = HTMLCache(compress=True)
    return _cache
