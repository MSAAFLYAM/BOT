"""
long_term_extractor.py
Extracts long-term buyer experiences from already-scraped Amazon reviews.
No extra API — works on the reviews[] scraped by scraper.py.
"""


def extract_long_term_experience(reviews: list[dict]) -> dict:
    """
    Args:
        reviews: list of reviews from scraper [{text, rating, date, verified, title}]

    Looks for reviews mentioning long-term usage:
    keywords: 'after X month', 'years', 'still using', 'long term', 'update:',
              'months later', '6 months', 'year later', 'daily use for'

    Returns:
    {
        "has_long_term_data": bool,
        "after_1_month": {"quote": str, "rating": int, "source": "amazon"},
        "after_3_months": {"quote": str, "rating": int, "source": "amazon"},
        "after_6_months": {"quote": str, "rating": int, "source": "amazon"},
        "overall_sentiment": "positive" | "negative" | "mixed",
        "durability_score": float  # 0-10 inferred from long reviews
    }
    """
    import re

    def _rrating(r) -> float:
        v = r.get('rating', r.get('stars', 0)) or 0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    LONG_TERM_PATTERNS = [
        r'\b(\d+)\s*month', r'\b(\d+)\s*year', r'still using',
        r'long.?term', r'months later', r'year later', r'update[:\s]',
        r'daily use for', r'after\s+(?:a\s+)?(?:few|several)\s+months'
    ]

    long_term_reviews = []
    for review in (reviews or []):
        text = review.get('text', '') or review.get('body', '') or ''
        if any(re.search(p, text, re.IGNORECASE) for p in LONG_TERM_PATTERNS):
            long_term_reviews.append(review)

    if not long_term_reviews:
        return {
            "has_long_term_data": False,
            "after_1_month": None,
            "after_3_months": None,
            "after_6_months": None,
            "overall_sentiment": "unknown",
            "durability_score": None
        }

    # Classify by mentioned duration
    def classify_duration(text: str) -> str:
        text_lower = text.lower()
        if any(p in text_lower for p in ['6 month', 'half year', '7 month', '8 month', '9 month', 'year']):
            return '6_months'
        elif any(p in text_lower for p in ['3 month', '4 month', '5 month']):
            return '3_months'
        else:
            return '1_month'

    buckets = {'1_month': [], '3_months': [], '6_months': []}
    for r in long_term_reviews:
        bucket = classify_duration(r.get('text', '') or r.get('body', ''))
        buckets[bucket].append(r)

    def best_quote(reviews_list: list, max_chars: int = 250) -> dict | None:
        if not reviews_list:
            return None
        # pick the longest, highest-rated review
        best = max(reviews_list, key=lambda r: len(r.get('text', '') or r.get('body', '')) + _rrating(r) * 10)
        text = (best.get('text', '') or best.get('body', ''))[:max_chars]
        if len(best.get('text', '') or best.get('body', '')) > max_chars:
            text += '...'
        return {
            "quote": text,
            "rating": int(_rrating(best)),
            "source": "amazon_verified_buyer"
        }

    positive = sum(1 for r in long_term_reviews if _rrating(r) >= 4)
    negative = sum(1 for r in long_term_reviews if _rrating(r) <= 2)
    sentiment = 'positive' if positive > negative * 2 else ('negative' if negative > positive * 2 else 'mixed')

    dur_score = sum(_rrating(r) for r in long_term_reviews) / len(long_term_reviews) * 2

    return {
        "has_long_term_data": True,
        "after_1_month": best_quote(buckets['1_month']),
        "after_3_months": best_quote(buckets['3_months']),
        "after_6_months": best_quote(buckets['6_months']),
        "overall_sentiment": sentiment,
        "durability_score": round(dur_score, 1)
    }