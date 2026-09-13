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
_MAX_CONSECUTIVE_429S = 10  # Stop after 10 consecutive rate limits


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
    import html as _h

    def _un(s: str) -> str:
        t = _h.unescape(s or "")
        for _ in range(3):
            u = _h.unescape(t)
            if u == t:
                break
            t = u
        return t.replace("�", "").strip()

    product = {"title": _un(title)}

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
        f = _un(f)
        if 10 < len(f) < 200 and "$" not in f and "Amazon" not in f:
            features.append(f)
    product["features"] = features[:6]

    return product


def fix_all(niche_only: bool = False, off_niche_only: bool = False, template_only: bool = False):
    if template_only:
        os.environ["GROQ_API_KEY"] = ""
        os.environ["OPENROUTER_API_KEY"] = ""
        logger.info("[mode] Template-only (free local, no API)")
    if not blogger.is_configured():
        logger.error("Blogger API not configured!")
        return

    # Get ALL posts (paginate)
    all_posts = []
    page_token = None
    while True:
        try:
            token = blogger._get_access_token()
            params = {"maxResults": 50, "status": "live"}
            if page_token:
                params["pageToken"] = page_token
            resp = blogger._api_request_with_retry(
                "GET",
                f"{blogger.BLOGGER_BASE}/blogs/{blogger.BLOG_ID}/posts",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=15,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("items", [])
            all_posts.extend(items)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        except Exception as e:
            logger.error(f"Error fetching posts: {e}")
            break
    posts = all_posts
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

        # Skip only fully-updated articles (new template uses rvw-wrapper class)
        if "rvw-wrapper" in content:
            logger.info(f"[{i}/{len(posts)}] SKIP (already updated): {title[:50]}")
            skipped += 1
            continue

        # Action 10A filter: niche vs off-niche per controlled labels
        _labels_tmp = blogger._map_labels(title)
        _is_niche = any(l in _labels_tmp for l in ["Smart Home", "Home Security", "Home Assistant", "Video Doorbells", "Security Cameras", "Smart Locks"])
        if niche_only and not _is_niche:
            skipped += 1
            continue
        if off_niche_only and _is_niche:
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
                time.sleep(5)
                logger.info("  Deleting old post...")
                blogger.delete_post(post_id)
                updated += 1
                _reset_rate_limit()
            else:
                error = str(result.get("error", ""))
                logger.error(f"  FAILED: {error}")
                errors += 1
                if "429" in error or "rateLimitExceeded" in error:
                    _increment_rate_limit()
                    wait_time = min(60 * _consecutive_429s, 600)
                    logger.info(f"  Rate limited — waiting {wait_time}s (consecutive: {_consecutive_429s})...")
                    time.sleep(wait_time)

        except Exception as e:
            logger.error(f"  ERROR: {e}")
            errors += 1

        # Longer delay between articles to stay under quota
        time.sleep(10)

    logger.info(f"\n{'='*50}")
    logger.info(f"SUMMARY: {updated} updated, {skipped} skipped, {errors} errors")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche-only", action="store_true", help="Only Smart Home / Home Security")
    ap.add_argument("--off-niche-only", action="store_true", help="Only off-niche (generic)")
    ap.add_argument("--template-only", action="store_true", help="Force local template (no Groq/OpenRouter)")
    args = ap.parse_args()
    fix_all(niche_only=args.niche_only, off_niche_only=args.off_niche_only, template_only=args.template_only)
