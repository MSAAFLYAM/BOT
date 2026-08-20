# queue_manager.py — File-backed product queue with deduplication
import json
import os
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

QUEUE_FILE = "product_queue.json"
_lock = threading.Lock()


def _load() -> dict:
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"pending": [], "posted_asins": []}


def _save(data: dict):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_product(product: dict, media_url: str, description: str,
                keywords: str, board: str) -> bool:
    """
    Add a product to the queue. Returns False if ASIN is a duplicate.
    product must have: title, price, aff_link, img_url, asin
    """
    asin = product.get("asin", "")
    with _lock:
        data = _load()
        if asin and asin in data["posted_asins"]:
            logger.info(f"[queue] Skipping duplicate ASIN: {asin}")
            return False
        # Also check pending queue
        existing_asins = [p.get("asin") for p in data["pending"]]
        if asin and asin in existing_asins:
            logger.info(f"[queue] Already in queue: {asin}")
            return False

        entry = {
            "title": product["title"][:100],
            "media_url": media_url,
            "board": board,
            "description": description,
            "aff_link": product["aff_link"],
            "price": product["price"],
            "img_url": product.get("img_url", ""),
            "asin": asin,
            "keywords": keywords,
            "added_at": datetime.utcnow().isoformat(),
        }
        data["pending"].append(entry)
        _save(data)
        logger.info(f"[queue] Added: {entry['title'][:50]} (ASIN: {asin})")
        return True


def pop_next() -> dict | None:
    """Remove and return the next pending product, or None if empty."""
    with _lock:
        data = _load()
        if not data["pending"]:
            return None
        item = data["pending"].pop(0)
        _save(data)
        return item


def mark_posted(asin: str):
    """Record an ASIN as posted so it's never re-queued."""
    with _lock:
        data = _load()
        if asin and asin not in data["posted_asins"]:
            data["posted_asins"].append(asin)
        _save(data)


def pending_count() -> int:
    return len(_load()["pending"])


def clear_queue():
    with _lock:
        data = _load()
        data["pending"] = []
        _save(data)
