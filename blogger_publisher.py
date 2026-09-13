# blogger_publisher.py â€” Article HTML builder shared by email publisher
# Used by: blogger_email_publisher.py â†’ _build_article()

import re
from datetime import datetime


def _stars(rating: float) -> str:
    full  = int(rating)
    half  = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return "â˜…" * full + ("Â½" if half else "") + "â˜†" * empty


def _build_article(product: dict, description: str) -> tuple:
    """
    Build (title, html) for Blogger post.
    Delegates to blogger_api_publisher (v4.2 theme structure).
    Kept for backward compatibility — do not duplicate templates here.
    """
    from blogger_api_publisher import _build_article as _build_v42
    return _build_v42(product, description)
