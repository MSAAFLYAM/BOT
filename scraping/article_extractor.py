"""
scraping/article_extractor.py — استخراج مقالات كاملة

يستخدم newspaper4k + trafilatura معاً:
  - newspaper4k: عنوان، نص، صورة، كاتب، تاريخ
  - trafilatura: نص أنظف وأكثر دقة

pip install newspaper4k trafilatura
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ArticleData:
    """بيانات مقال مستخرج."""
    title:    str   = ""
    text:     str   = ""
    image:    str   = ""
    summary:  str   = ""
    keywords: list  = None
    authors:  list  = None
    date:     str   = ""
    language: str   = "fr"
    source:   str   = ""   # "newspaper4k" | "trafilatura" | "basic"

    def __post_init__(self):
        self.keywords = self.keywords or []
        self.authors  = self.authors  or []

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def is_good(self) -> bool:
        """هل المقال جيد بما يكفي للمعالجة؟"""
        return len(self.text) > 300 and bool(self.title)


async def extract_article(url: str) -> ArticleData:
    """
    استخراج مقال كامل من URL.
    يجرب newspaper4k أولاً ثم trafilatura كـ fallback.
    """

    # ── Newspaper4k (الأفضل للمقالات) ─────────────────────────────────────
    result = await _extract_newspaper(url)
    if result and result.is_good:
        return result

    # ── Trafilatura (fallback) ────────────────────────────────────────────
    result = await _extract_trafilatura(url)
    if result and result.is_good:
        return result

    # ── Basic fallback ─────────────────────────────────────────────────────
    return ArticleData(source="failed")


async def _extract_newspaper(url: str) -> Optional[ArticleData]:
    """استخراج بـ newspaper4k."""
    try:
        import asyncio
        from newspaper import Article

        def _sync():
            article = Article(url, language="fr")
            article.download()
            article.parse()
            try:
                article.nlp()
            except Exception:
                pass
            return article

        loop    = asyncio.get_event_loop()
        article = await loop.run_in_executor(None, _sync)

        if not article.text or len(article.text) < 200:
            return None

        return ArticleData(
            title    = article.title        or "",
            text     = article.text         or "",
            image    = article.top_image    or "",
            summary  = article.summary      or "",
            keywords = list(article.keywords or [])[:10],
            authors  = list(article.authors  or []),
            date     = str(article.publish_date or ""),
            source   = "newspaper4k",
        )

    except ImportError:
        logger.debug("[extractor] newspaper4k non installé")
        return None
    except Exception as e:
        logger.debug(f"[extractor] newspaper4k error: {e}")
        return None


async def _extract_trafilatura(url: str) -> Optional[ArticleData]:
    """استخراج بـ trafilatura."""
    try:
        import asyncio
        import trafilatura
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            html = resp.text

        loop = asyncio.get_event_loop()

        def _sync():
            text = trafilatura.extract(
                html,
                include_comments = False,
                include_tables   = True,
                favor_recall     = True,
            )
            meta = trafilatura.extract_metadata(html)
            return text, meta

        text, meta = await loop.run_in_executor(None, _sync)

        if not text or len(text) < 200:
            return None

        return ArticleData(
            title  = (meta.title  if meta else "") or "",
            text   = text,
            image  = (meta.image  if meta else "") or "",
            date   = str(meta.date if meta else "") or "",
            source = "trafilatura",
        )

    except ImportError:
        logger.debug("[extractor] trafilatura non installé")
        return None
    except Exception as e:
        logger.debug(f"[extractor] trafilatura error: {e}")
        return None
