"""
scraping/fetch/jina_reader.py — Jina AI Reader

Layer 0 في cascade الـ scraping.
يحوّل أي URL إلى Markdown نظيف في ثانية.

مجاني تماماً — لا يحتاج API key.
يعمل على 80% من المواقع بدون Playwright.

الفائدة:
  - أسرع بـ 10x من Playwright
  - يوفر ذاكرة RAM في بيئات النشر المختلفة
  - نص نظيف جاهز للـ AI مباشرة
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

JINA_BASE    = "https://r.jina.ai/"
TIMEOUT_S    = 15
MIN_LENGTH   = 300   # أقل من هذا = صفحة فارغة


async def fetch_with_jina(url: str) -> Optional[str]:
    """
    جلب محتوى URL كـ Markdown نظيف عبر Jina AI Reader.

    Returns:
        Markdown string إذا نجح
        None إذا فشل (نكمل للـ layer التالي)
    """
    try:
        jina_url = f"{JINA_BASE}{url}"
        async with httpx.AsyncClient(
            timeout      = TIMEOUT_S,
            follow_redirects = True,
            headers      = {
                "Accept":          "text/plain",
                "X-Return-Format": "markdown",
            },
        ) as client:
            resp = await client.get(jina_url)

            if resp.status_code != 200:
                logger.debug(f"[jina] {url} → HTTP {resp.status_code}")
                return None

            content = resp.text.strip()

            if len(content) < MIN_LENGTH:
                logger.debug(f"[jina] {url} → trop court ({len(content)} chars)")
                return None

            logger.info(f"[jina] ✅ {url} → {len(content)} chars")
            return content

    except httpx.TimeoutException:
        logger.debug(f"[jina] timeout: {url}")
        return None
    except Exception as e:
        logger.debug(f"[jina] error: {url} → {e}")
        return None


async def is_jina_available() -> bool:
    """تحقق سريع أن Jina متاح."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{JINA_BASE}https://example.com")
            return resp.status_code == 200
    except Exception:
        return False
