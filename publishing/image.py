"""
publishing/image.py — Image compression via TinyPNG + Redis cache.

Architecture decisions:
  - TinyPNG API: https://api.tinify.com/shrink
    Auth: HTTP Basic "api:{API_KEY}"
    Send source URL → Tinify downloads + compresses → returns output URL
    Then download compressed image bytes for upload to WP/Blogger.

  - Credit conservation (500/month limit):
    Only compress images > 100KB (smaller images don't benefit much).
    Cache compressed result in Redis (TTL 7 days) → same image never
    compressed twice in a month. Key = SHA-256(original_url).

  - Why cache compressed bytes AND URL:
    WordPress needs raw bytes (multipart upload).
    Blogger accepts URLs directly (embed in post HTML).
    Storing both avoids double API calls.

  - Fallback: if TinyPNG fails (quota exceeded, timeout), use original URL.
    Publishing must never fail due to image compression failure.

  - Stats tracking: log credits used, compression ratio, bytes saved.
    Important to monitor monthly quota usage.

Credit estimation:
  500 credits/month ÷ 30 days = ~16 images/day max.
  With caching: each unique image counts once → efficient use.
  Threshold 100KB filters ~30% of images → effective ~230 unique compressions.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
TINIFY_API_URL    = "https://api.tinify.com/shrink"
SIZE_THRESHOLD_KB = 100        # Only compress images above this size
CACHE_TTL         = 86400 * 7  # 7 days cache in Redis
CACHE_PREFIX      = "tinify:compressed:"


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class CompressedImage:
    """Result of image compression."""
    original_url:    str
    compressed_url:  str              = ""
    compressed_bytes:Optional[bytes]  = None
    original_size:   int              = 0   # bytes
    compressed_size: int              = 0   # bytes
    was_compressed:  bool             = False
    from_cache:      bool             = False
    error:           Optional[str]    = None

    @property
    def saved_kb(self) -> float:
        return (self.original_size - self.compressed_size) / 1024

    @property
    def ratio(self) -> float:
        if not self.original_size:
            return 0.0
        return 1.0 - (self.compressed_size / self.original_size)

    @property
    def best_url(self) -> str:
        """Return compressed URL if available, else original."""
        return self.compressed_url or self.original_url


# ── TinyPNG Client ────────────────────────────────────────────────────────────

class TinyPNGClient:
    """
    Async TinyPNG image compression client.

    Usage:
        client = TinyPNGClient(api_key="your_key")
        result = await client.compress_from_url("https://example.com/image.jpg")
        if result.was_compressed:
            upload_to_wordpress(result.compressed_bytes)
        else:
            upload_from_url(result.original_url)
    """

    def __init__(self, api_key: Optional[str] = None):
        if api_key:
            self._api_key = api_key
        else:
            import os
            self._api_key = os.environ.get("TINIFY_API_KEY", "")

        # Basic auth header: "api:{key}" encoded in base64
        credentials  = base64.b64encode(f"api:{self._api_key}".encode()).decode()
        self._auth   = f"Basic {credentials}"

    def _cache_key(self, url: str) -> str:
        """Stable cache key from URL."""
        return CACHE_PREFIX + hashlib.sha256(url.encode()).hexdigest()[:32]

    async def compress_from_url(
        self,
        image_url:        str,
        force:            bool = False,
        download_result:  bool = True,
    ) -> CompressedImage:
        """
        Compress an image from URL using TinyPNG.

        Steps:
          1. Check Redis cache (return cached if found)
          2. Check image size (skip if < SIZE_THRESHOLD_KB)
          3. Send URL to TinyPNG API
          4. Download compressed image bytes
          5. Cache result in Redis
          6. Return CompressedImage

        Args:
            image_url:       Original image URL
            force:           Skip cache, force re-compression
            download_result: Download compressed bytes (needed for WP upload)

        Returns:
            CompressedImage (was_compressed=False if skipped or failed)
        """
        result = CompressedImage(original_url=image_url)

        if not image_url or not image_url.startswith("http"):
            result.error = "Invalid image URL"
            return result

        # Step 1: Cache check
        if not force:
            cached = await self._get_cache(image_url)
            if cached:
                logger.debug(f"[tinify] Cache HIT: {image_url[:60]}")
                return cached

        try:
            async with httpx.AsyncClient(timeout=30) as client:

                # Step 2: Check original image size
                try:
                    head = await client.head(image_url, follow_redirects=True)
                    content_len = int(head.headers.get("content-length", 0))
                    result.original_size = content_len
                except Exception:
                    content_len = 0

                # Skip small images (save credits)
                if content_len and content_len < SIZE_THRESHOLD_KB * 1024:
                    logger.debug(
                        f"[tinify] Skipped (too small: {content_len/1024:.0f}KB): "
                        f"{image_url[:60]}"
                    )
                    result.compressed_url = image_url
                    return result

                # Step 3: Send to TinyPNG
                logger.info(f"[tinify] Compressing: {image_url[:60]}")
                resp = await client.post(
                    TINIFY_API_URL,
                    headers={
                        "Authorization": self._auth,
                        "Content-Type":  "application/json",
                    },
                    json={"source": {"url": image_url}},
                )

                if resp.status_code == 201:
                    data = resp.json()
                    output      = data.get("output", {})
                    comp_url    = output.get("url", "")
                    comp_size   = output.get("size", 0)
                    orig_size   = data.get("input", {}).get("size", content_len)

                    result.compressed_url  = comp_url
                    result.compressed_size = comp_size
                    result.original_size   = orig_size
                    result.was_compressed  = True

                    saved_pct = result.ratio * 100
                    logger.info(
                        f"[tinify] ✅ Compressed: {orig_size/1024:.0f}KB → "
                        f"{comp_size/1024:.0f}KB (-{saved_pct:.0f}%)"
                    )

                    # Step 4: Download compressed bytes
                    if download_result and comp_url:
                        dl = await client.get(comp_url)
                        result.compressed_bytes = dl.content

                    # Step 5: Cache result
                    await self._set_cache(image_url, result)

                    return result

                elif resp.status_code == 429:
                    logger.warning("[tinify] ❌ Monthly quota exceeded (500 credits)")
                    result.error = "TinyPNG quota exceeded"
                    result.compressed_url = image_url  # Fallback to original
                    return result

                elif resp.status_code == 401:
                    logger.error("[tinify] ❌ Invalid API key")
                    result.error = "Invalid TinyPNG API key"
                    result.compressed_url = image_url
                    return result

                else:
                    logger.warning(
                        f"[tinify] HTTP {resp.status_code}: {resp.text[:100]}"
                    )
                    result.error          = f"HTTP {resp.status_code}"
                    result.compressed_url = image_url
                    return result

        except httpx.TimeoutException:
            logger.warning(f"[tinify] Timeout for: {image_url[:60]}")
            result.error          = "Timeout"
            result.compressed_url = image_url
            return result

        except Exception as e:
            logger.warning(f"[tinify] Error: {e}")
            result.error          = str(e)[:100]
            result.compressed_url = image_url
            return result

    async def get_remaining_credits(self) -> Optional[int]:
        """
        Check remaining TinyPNG credits.
        Returns None if API call fails.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.tinify.com/",
                    headers={"Authorization": self._auth},
                )
                if resp.status_code in (200, 401, 429):
                    # Credits info is in response header
                    used = resp.headers.get("compression-count")
                    if used:
                        return 500 - int(used)  # 500 = monthly limit
        except Exception:
            pass
        return None

    # ── Cache helpers ─────────────────────────────────────────────────────────

    async def _get_cache(self, url: str) -> Optional[CompressedImage]:
        """Load cached compression result from Redis."""
        try:
            from core.redis_client import get_redis
            redis = get_redis()
            data  = await redis.get(self._cache_key(url))
            if data:
                d = json.loads(data)
                result = CompressedImage(
                    original_url=d["original_url"],
                    compressed_url=d["compressed_url"],
                    original_size=d.get("original_size", 0),
                    compressed_size=d.get("compressed_size", 0),
                    was_compressed=d.get("was_compressed", False),
                    from_cache=True,
                )
                # Re-download bytes if needed (not cached to save Redis memory)
                if result.compressed_url and result.compressed_url != url:
                    try:
                        async with httpx.AsyncClient(timeout=15) as client:
                            dl = await client.get(result.compressed_url)
                            result.compressed_bytes = dl.content
                    except Exception:
                        pass
                return result
        except Exception:
            pass
        return None

    async def _set_cache(self, url: str, result: CompressedImage) -> None:
        """Cache compression result in Redis (without bytes — too large)."""
        try:
            from core.redis_client import get_redis
            redis = get_redis()
            data  = json.dumps({
                "original_url":   result.original_url,
                "compressed_url": result.compressed_url,
                "original_size":  result.original_size,
                "compressed_size":result.compressed_size,
                "was_compressed": result.was_compressed,
            })
            await redis.setex(self._cache_key(url), CACHE_TTL, data)
        except Exception as e:
            logger.warning(f"[tinify] Cache save failed: {e}")


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: Optional[TinyPNGClient] = None


def get_tinify_client() -> TinyPNGClient:
    """Return module-level TinyPNG client singleton."""
    global _client
    if _client is None:
        _client = TinyPNGClient()
    return _client
