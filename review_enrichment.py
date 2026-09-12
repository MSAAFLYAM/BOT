"""
review_enrichment.py -- Real buyer feedback for strict, honest reviews.

Fully automated, free, no API keys:
  1. Amazon buyer reviews already scraped (critical 1-3 stars included)
  2. Reddit public search (best-effort, degrades gracefully to "")

Output is a prompt block injected into the AI review request so that
pros/cons/verdict/scores reflect REAL user complaints -- never invented.
Quora is intentionally skipped (login wall blocks automation).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_REDDIT_URL = "https://www.reddit.com/search.json"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NestDealBot/1.0 (review research)"


def _short_query(title: str) -> str:
    """Keep the distinctive product words for Reddit search."""
    stop = {"the", "with", "and", "for", "from", "plus", "pack", "new",
            "best", "top", "pro", "smart", "digital", "electric"}
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title or "")
             if len(w) > 2 and w.lower() not in stop]
    return " ".join(words[:6])


def _reddit_quotes(query: str, limit: int = 5) -> list[str]:
    """Best-effort Reddit search. Returns [] on any failure."""
    if not query:
        return []
    try:
        import requests
        r = requests.get(
            _REDDIT_URL,
            params={"q": query + " review", "limit": limit,
                    "sort": "relevance", "t": "year"},
            headers={"User-Agent": _UA},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        out = []
        for c in (r.json().get("data", {}).get("children", []) or [])[:limit]:
            d = c.get("data", {}) or {}
            title = (d.get("title") or "").strip()
            body = (d.get("selftext") or "").strip()[:220]
            sub = d.get("subreddit_name_prefixed", "")
            score = d.get("score", 0)
            if not title or d.get("over_18"):
                continue
            txt = f"[r/{sub} +{score}] {title}"
            if body:
                txt += f" -- {body}"
            out.append(txt[:300])
        return out
    except Exception as e:
        logger.info(f"[enrich] Reddit skipped: {e}")
        return []


def gather(product: dict) -> str:
    """Build the REAL USER FEEDBACK prompt block ("" if nothing found)."""
    blocks = []

    # 1. Amazon buyer reviews (critical included by scraper)
    amazon = []
    for r in (product.get("customer_reviews") or [])[:6]:
        try:
            stars = float(r.get("stars", 0))
        except (ValueError, TypeError):
            stars = 0
        line = f"[{stars:.0f}/5] {(r.get('title') or '').strip()}"
        body = (r.get("body") or "").strip()[:200]
        if body:
            line += f": {body}"
        if line.strip("[]/5 :"):
            amazon.append(line[:280])
    if amazon:
        blocks.append("AMAZON BUYER REVIEWS:\n" + "\n".join(f"- {a}" for a in amazon))

    # 2. Reddit community feedback
    quotes = _reddit_quotes(_short_query(product.get("title", "")))
    if quotes:
        blocks.append("REDDIT COMMUNITY FEEDBACK:\n" + "\n".join(f"- {q}" for q in quotes))

    if not blocks:
        return ""
    return ("REAL USER FEEDBACK (ground your review in this -- "
            "complaints you see repeated MUST appear in cons and lower the scores):\n"
            + "\n\n".join(blocks))
