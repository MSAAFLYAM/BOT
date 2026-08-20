"""
pinterest/image_downloader.py
Download images to temp files for Pinterest upload, with network retry.
"""
from __future__ import annotations
import asyncio
import logging
import os
import tempfile
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def download_image(
    url:      str,
    attempts: int = 3,
    timeout:  int = 20,
) -> Optional[str]:
    """
    Download an image to a temp file with retry.
    Returns the temp file path, or None on failure.
    Caller is responsible for deleting the temp file.
    """
    if not url or not url.startswith("http"):
        logger.warning(f"[img_dl] Invalid URL: {url[:50]}")
        return None

    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                r = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/131.0.0.0 Safari/537.36",
                })
                if r.status_code == 200 and len(r.content) > 1000:
                    ctype = r.headers.get("content-type", "")
                    ext = ".png" if "png" in ctype else ".jpg"
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                    tmp.write(r.content)
                    tmp.close()
                    logger.info(f"[img_dl] ✅ Downloaded {len(r.content)//1024}KB → {tmp.name}")
                    return tmp.name
                else:
                    logger.warning(f"[img_dl] HTTP {r.status_code}, size={len(r.content)} (attempt {attempt})")
        except Exception as e:
            logger.warning(f"[img_dl] attempt {attempt}/{attempts} failed: {e}")

        if attempt < attempts:
            await asyncio.sleep(2 * attempt)

    logger.error(f"[img_dl] ❌ All {attempts} attempts failed for {url[:60]}")
    return None


def cleanup_temp(path: Optional[str]) -> None:
    """Safely delete a temp image file."""
    if path:
        try:
            os.unlink(path)
        except Exception:
            pass
