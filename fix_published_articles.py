"""Fix all published articles — republish with new NestDeal template."""
import os, sys, re, time, logging

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

import blogger_api_publisher as blogger
import scraper

# Disable Telegram alerts during bulk fix
blogger.SKIP_TELEGRAM_ALERT = True

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Rate limit tracking
_consecutive_429s = 0
_MAX_CONSECUTIVE_429S = 5  # Stop after 5 consecutive rate limits


def _check_rate_limit():
    """Check if we should pause due to rate limiting."""
    global _consecutive_429s
    if _consecutive_429s >= _MAX_CONSECUTIVE_429S:
        logger.error(f"Too many consecutive 429 errors ({_consecutive_429s}). Stopping to avoid quota exhaustion.")
        return False
    return True


def _reset_rate_limit():
    """Reset consecutive 429 counter on success."""
    global _consecutive_429s
    _consecutive_429s = 0


def _increment_rate_limit():
    """Increment consecutive 429 counter."""
    global _consecutive_429s
    _consecutive_429s += 1


def _extract_product(html: str, title: str) -> dict | None:
    product = {"title": title}

    asin_match = re.search(r'amazon\.com/dp/([A-Z0-9]{10})', html)
    if not asin_match:
        asin_match = re.search(r'amazon\.com/[^"\']*?([A-Z0-9]{10})', html)
    if asin_match:
        product["asin"] = asin_match.group(1)
        product["clean_url"] = f"https://www.amazon.com/dp/{product['asin']}"
        product["aff_link"] = scraper.build_affiliate_url(product["clean_url"])
    else:
        return None

    img_match = re.search(r'<img[^>]+src="([^"]+)"', html)
    product["img_url"] = img_match.group(1) if img_match else ""
    # For existing articles, set all_images to just the main image
    product["all_images"] = [product["img_url"]] if product["img_url"] else []
    # Customer reviews can't be extracted from existing articles
    product["customer_reviews"] = []

    price_match = re.search(r'\$(\d+\.?\d*)', html)
    product["price"] = f"${price_match.group(1)}" if price_match else "N/A"

    rating_match = re.search(r'(\d\.?\d?)/5', html)
    product["rating"] = float(rating_match.group(1)) if rating_match else 0

    review_match = re.search(r'([\d,]+)\s*reviews?', html, re.IGNORECASE)
    try:
        product["review_count"] = int(review_match.group(1).replace(",", "")) if review_match else 0
    except (ValueError, AttributeError):
        product["review_count"] = 0

    features = []
    for f in re.findall(r'<li[^>]*>([^<]+)</li>', html):
        f = f.strip()
        if 10 < len(f) < 200 and "$" not in f and "Amazon" not in f:
            features.append(f)
    product["features"] = features[:6]

    return product


def fix_all():
    if not blogger.is_configured():
        logger.error("Blogger API not configured!")
        return

    posts = blogger.list_recent_posts(max_results=50)
    logger.info(f"Found {len(posts)} articles to update")

    updated = skipped = errors = 0

    for i, post in enumerate(posts, 1):
        # Check rate limit before each article
        if not _check_rate_limit():
            logger.info("Rate limit reached — stopping. Run again later to continue.")
            break

        post_id = post.get("id", "")
        title = post.get("title", "")
        content = post.get("content", "")
        url = post.get("url", "")

        # Skip only fully-updated articles (new template uses nd-article class)
        if "nd-article" in content or "nd-img-grid" in content:
            logger.info(f"[{i}/{len(posts)}] SKIP (already updated): {title[:50]}")
            skipped += 1
            continue

        logger.info(f"\n[{i}/{len(posts)}] {title[:70]}")

        try:
            product = _extract_product(content, title)
            if not product:
                logger.warning("  SKIP - no ASIN found")
                skipped += 1
                continue

            logger.info(f"  ASIN: {product['asin']} | Building new article...")
            new_title, new_html = blogger._build_article(product, "")

            issues = blogger._validate_article(new_html, new_title)
            if issues:
                logger.warning(f"  Validation: {issues}")

            logger.info("  Deleting old post...")
            blogger.delete_post(post_id)
            time.sleep(10)  # Increased from 8s

            logger.info("  Publishing new article...")
            result = blogger.publish_post(
                product=product,
                description="",
                labels=blogger._map_labels(product["title"]),
                html_content=new_html,
                title=new_title,
                publish_now=True,
            )

            if result.get("status") == "success":
                logger.info(f"  DONE: {result.get('post_url', '')}")
                updated += 1
                _reset_rate_limit()  # Reset on success
            else:
                error = str(result.get("error", ""))
                logger.error(f"  FAILED: {error}")
                errors += 1
                # If rate limited, wait extra time and track
                if "429" in error or "rateLimitExceeded" in error:
                    _increment_rate_limit()
                    wait_time = min(120 * _consecutive_429s, 600)  # 120s, 240s, 360s, max 600s
                    logger.info(f"  Rate limited — waiting {wait_time}s (consecutive: {_consecutive_429s})...")
                    time.sleep(wait_time)

        except Exception as e:
            logger.error(f"  ERROR: {e}")
            errors += 1

        # Longer delay between articles to stay under quota
        time.sleep(15)  # Increased from 10s

    logger.info(f"\n{'='*50}")
    logger.info(f"SUMMARY: {updated} updated, {skipped} skipped, {errors} errors")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    fix_all()
