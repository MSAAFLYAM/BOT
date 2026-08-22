"""
daily_scheduler.py -- Auto-Discovery & Publishing Scheduler

Fully automated product discovery and publishing.
No human intervention needed. Runs 24/7 on cloud.

Rules:
  1. Mix all niches per session -- pick 1-2 products per category
  2. Max 10 articles/day total (tracked via JSON file)
  3. Skip failed keywords -- never retry endlessly
  4. Never exceed 10 products/day under any circumstance
  5. Stop when daily limit reached, resume next day
  6. Telegram notification for every success/failure

Env vars:
  AUTO_DISCOVER_ENABLED=true
  AUTO_DISCOVER_INTERVAL_HOURS=4
  AUTO_ARTICLES_PER_RUN=3
  AUTO_DAILY_MAX=10
  AUTO_KEYWORDS={"Home & Kitchen": ["air fryer", ...], ...}
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# -- Daily limit tracking --
DAILY_LIMIT_FILE = "daily_publish_limit.json"
DAILY_MAX = int(os.environ.get("AUTO_DAILY_MAX", "10"))
_lock = threading.Lock()


def _load_daily_limit() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if os.path.exists(DAILY_LIMIT_FILE):
            with open(DAILY_LIMIT_FILE, "r") as f:
                data = json.load(f)
            if data.get("date") == today:
                return data
    except Exception:
        pass
    return {"date": today, "published": 0, "failed": 0, "skipped": 0, "asins": []}


def _save_daily_limit(data: dict):
    with open(DAILY_LIMIT_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _increment_daily(count: int = 1) -> int:
    with _lock:
        data = _load_daily_limit()
        data["published"] += count
        _save_daily_limit(data)
        return data["published"]


def _get_daily_count() -> int:
    with _lock:
        data = _load_daily_limit()
        return data["published"]


def _daily_limit_reached() -> bool:
    return _get_daily_count() >= DAILY_MAX


def _record_asin(asin: str):
    with _lock:
        data = _load_daily_limit()
        if asin not in data.get("asins", []):
            data.setdefault("asins", []).append(asin)
        _save_daily_limit(data)


def _is_asin_used(asin: str) -> bool:
    with _lock:
        data = _load_daily_limit()
        return asin in data.get("asins", [])


# -- Default keywords by category --
DEFAULT_KEYWORDS = {
    "Home & Kitchen": [
        "air fryer", "robot vacuum", "instant pot", "coffee maker",
        "knife set", "stand mixer", "food storage containers",
        "electric kettle", "cast iron skillet", "blender",
    ],
    "Beauty & Health": [
        "facial cleansing brush", "hair dryer", "electric toothbrush",
        "massage gun", "skincare fridge", "jade roller",
        "teeth whitening kit", "humidifier", "posture corrector", "foot spa",
    ],
    "Sports & Outdoors": [
        "yoga mat", "resistance bands", "camping tent",
        "hydration backpack", "fitness tracker", "foldable bike",
        "insulated water bottle", "hiking boots", "portable grill", "sleeping bag",
    ],
    "Tech & Gadgets": [
        "led strip lights", "portable charger", "bluetooth speaker",
        "wireless earbuds", "phone case", "laptop stand",
        "smart plug", "webcam", "mechanical keyboard", "tablet stand",
    ],
}

# -- Scheduler state --
_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()


def start(bot, admin_chat_id: int) -> bool:
    """Start auto-discovery scheduler."""
    global _scheduler_thread, _stop_event

    enabled = os.environ.get("AUTO_DISCOVER_ENABLED", "false").lower()
    if enabled != "true":
        logger.info("[auto] AUTO_DISCOVER_ENABLED != true -- scheduler disabled")
        return False

    if _scheduler_thread and _scheduler_thread.is_alive():
        logger.info("[auto] Scheduler already running")
        return False

    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(bot, admin_chat_id),
        daemon=True,
        name="auto-discover",
    )
    _scheduler_thread.start()
    logger.info(f"[auto] Scheduler started -- admin={admin_chat_id}")
    return True


def stop() -> None:
    _stop_event.set()
    logger.info("[auto] Scheduler stopped")


def is_running() -> bool:
    return _scheduler_thread is not None and _scheduler_thread.is_alive()


def get_interval() -> int:
    return int(os.environ.get("AUTO_DISCOVER_INTERVAL_HOURS", "4"))


# -- Main loop --

def _scheduler_loop(bot, admin_chat_id: int) -> None:
    logger.info("[auto] Loop started")

    while not _stop_event.is_set():
        try:
            if _daily_limit_reached():
                logger.info(f"[auto] Daily limit ({DAILY_MAX}) reached -- sleeping until tomorrow")
                _send_daily_summary(bot, admin_chat_id)
                _sleep_until_tomorrow()
                continue

            _run_session(bot, admin_chat_id)
        except Exception as e:
            logger.error(f"[auto] Loop error: {e}")

        interval_hours = get_interval()
        sleep_seconds = interval_hours * 3600
        logger.info(f"[auto] Next run in {interval_hours}h")
        for _ in range(int(sleep_seconds / 5)):
            if _stop_event.is_set():
                break
            time.sleep(5)


def _sleep_until_tomorrow():
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    delay = (tomorrow - now).total_seconds()
    logger.info(f"[auto] Sleeping {delay/3600:.1f}h until tomorrow")
    while delay > 0 and not _stop_event.is_set():
        time.sleep(min(60, delay))
        delay -= 60


def _send_daily_summary(bot, admin_chat_id: int):
    try:
        data = _load_daily_limit()
        bot.send_message(
            admin_chat_id,
            f"<b>Daily limit reached ({DAILY_MAX})</b>\n"
            f"Published today: <b>{data['published']}</b>\n"
            f"Failed: <b>{data.get('failed', 0)}</b>\n"
            f"Skipped: <b>{data.get('skipped', 0)}</b>\n"
            f"Resuming tomorrow at midnight",
            parse_mode="HTML",
        )
    except Exception:
        pass


# -- Session runner --

def _run_session(bot, admin_chat_id: int):
    import scraper
    import blogger_api_publisher as blogger
    import content_generator

    # Load keywords
    keywords_env = os.environ.get("AUTO_KEYWORDS", "")
    if keywords_env:
        try:
            categories = json.loads(keywords_env)
        except Exception:
            categories = DEFAULT_KEYWORDS
    else:
        categories = DEFAULT_KEYWORDS

    articles_per_run = int(os.environ.get("AUTO_ARTICLES_PER_RUN", "3"))
    daily_remaining = DAILY_MAX - _get_daily_count()
    to_publish = min(articles_per_run, daily_remaining)

    if to_publish <= 0:
        logger.info("[auto] No articles left to publish today")
        return

    # Build keyword list: mix all categories
    all_keywords = []
    for cat, kws in categories.items():
        for kw in kws:
            all_keywords.append({"category": cat, "keyword": kw})
    random.shuffle(all_keywords)

    published = 0
    tried = 0

    # Notify session start
    try:
        bot.send_message(
            admin_chat_id,
            f"<b>Auto-discovery session started</b>\n"
            f"Target: <b>{to_publish}</b> articles\n"
            f"Remaining today: <b>{daily_remaining}</b>/<b>{DAILY_MAX}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    for item in all_keywords:
        if published >= to_publish:
            break
        if _daily_limit_reached():
            break
        if _stop_event.is_set():
            break

        category = item["category"]
        keyword = item["keyword"]
        tried += 1

        logger.info(f"[auto] [{tried}] Searching: {keyword} ({category})")

        try:
            results = scraper.search_amazon(
                keyword, max_results=3, min_rating=4.0, min_reviews=50
            )
            if not results:
                logger.info(f"[auto] No results for '{keyword}' -- skipping")
                with _lock:
                    d = _load_daily_limit()
                    d["skipped"] = d.get("skipped", 0) + 1
                    _save_daily_limit(d)
                continue

            product = results[0]
            asin = product.get("asin", "")

            if _is_asin_used(asin):
                logger.info(f"[auto] ASIN {asin} already published -- skipping")
                continue

            if not product.get("img_url"):
                logger.info(f"[auto] No image for '{keyword}' -- skipping")
                continue

            description = content_generator.generate_description(
                product["title"], product["price"]
            )

            logger.info(f"[auto] Publishing: {product['title'][:60]}...")
            board = content_generator.map_pinterest_board(product["title"])
            result = blogger.publish_post(
                product=product,
                description=description,
                labels=[board] if board else [],
                publish_now=True,
            )

            if result.get("status") == "success":
                published += 1
                post_url = result.get("post_url", "")
                _increment_daily()
                _record_asin(asin)

                # Success notification
                try:
                    bot.send_message(
                        admin_chat_id,
                        f"<b>Auto-published ({published}/{to_publish})</b>\n"
                        f"{'='*24}\n"
                        f"<b>{product['title'][:70]}</b>\n"
                        f"Price: {product.get('price', 'N/A')}\n"
                        f"Rating: {product.get('rating', 0)}/5 -- "
                        f"{product.get('review_count', 0):,} reviews\n"
                        f"Category: {category}\n"
                        f"{'='*24}\n"
                        f"Blogger: <a href=\"{post_url}\">View Article</a>\n"
                        f"Amazon: <a href=\"{product.get('aff_link', '')}\">Product Page</a>\n"
                        f"{'='*24}\n"
                        f"Today: {_get_daily_count()}/{DAILY_MAX} published",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

                logger.info(f"[auto] Published: {product['title'][:50]} -> {post_url}")
            else:
                error = str(result.get("error", "Unknown"))
                logger.error(f"[auto] Failed: {error[:100]}")
                with _lock:
                    d = _load_daily_limit()
                    d["failed"] = d.get("failed", 0) + 1
                    _save_daily_limit(d)

                # Failure notification
                try:
                    bot.send_message(
                        admin_chat_id,
                        f"<b>Auto-publish FAILED</b>\n"
                        f"{'='*24}\n"
                        f"<b>{product['title'][:70]}</b>\n"
                        f"Keyword: {keyword}\n"
                        f"Category: {category}\n"
                        f"Error: <code>{error[:150]}</code>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"[auto] Error processing '{keyword}': {e}")
            with _lock:
                d = _load_daily_limit()
                d["failed"] = d.get("failed", 0) + 1
                _save_daily_limit(d)
            try:
                bot.send_message(
                    admin_chat_id,
                    f"<b>Auto-discover error</b>\n"
                    f"Keyword: <code>{keyword}</code>\n"
                    f"Category: {category}\n"
                    f"Error: <code>{str(e)[:150]}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        # Delay between products (avoid rate limits)
        time.sleep(8)

    # Session summary
    try:
        bot.send_message(
            admin_chat_id,
            f"<b>Session complete</b>\n"
            f"Published: <b>{published}</b>/{to_publish}\n"
            f"Tried: {tried} keywords\n"
            f"Today total: <b>{_get_daily_count()}/{DAILY_MAX}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass
