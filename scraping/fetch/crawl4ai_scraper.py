"""
scraping/fetch/crawl4ai_scraper.py — crawl4ai AI-powered scraper

أقوى scraper بالـ AI — مصمم خصيصاً للـ LLMs.
يفهم السياق ويستخرج البيانات المنظمة مباشرة.

الـ repo: github.com/unclecode/crawl4ai
السرعة:  6x أسرع من Playwright
الفائدة: يفهم السياق، يستخرج JSON مباشرة

التثبيت:
    pip install crawl4ai
    playwright install chromium

يُستخدم كـ Layer 0.5 — بعد Jina وقبل curl-cffi.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def scrape_with_crawl4ai(url: str) -> Optional[str]:
    """
    Scraping بـ crawl4ai — يستخرج Markdown نظيف.
    Fallback لـ Jina إذا فشل crawl4ai.
    """
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig

        config = CrawlerRunConfig(
            word_count_threshold    = 50,
            remove_overlay_elements = True,
            process_iframes         = False,
        )

        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(url=url, config=config)

            if result.success and result.markdown:
                content = result.markdown.strip()
                if len(content) > 300:
                    logger.info(f"[crawl4ai] ✅ {url[:60]} → {len(content)} chars")
                    return content

        return None

    except ImportError:
        logger.debug("[crawl4ai] غير مثبت — pip install crawl4ai")
        return None
    except Exception as e:
        logger.debug(f"[crawl4ai] error: {e}")
        return None


async def extract_structured_data(url: str, schema: dict) -> Optional[dict]:
    """
    استخراج بيانات منظمة حسب schema محدد.
    مثال: استخراج المكونات، وقت الطهي، عدد الأشخاص من صفحة وصفة.
    """
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
        from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

        strategy = JsonCssExtractionStrategy(schema=schema)
        config   = CrawlerRunConfig(extraction_strategy=strategy)

        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(url=url, config=config)
            if result.success and result.extracted_content:
                import json
                return json.loads(result.extracted_content)

    except ImportError:
        logger.debug("[crawl4ai] غير مثبت")
    except Exception as e:
        logger.debug(f"[crawl4ai] structured extract error: {e}")

    return None
