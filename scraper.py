# scraper.py — 2026 Ultra Anti-Detection Amazon Scraper (FIXED)
# ================================================================

import re, time, random, logging, json, gzip
from urllib.parse import quote, urlparse

try:
    from curl_cffi import requests as curl_requests
    CURL_OK = True
except ImportError:
    import requests as curl_requests
    CURL_OK = False
    logging.warning("curl_cffi غير مثبت — pip install curl-cffi")

from bs4 import BeautifulSoup

try:
    import config
except ImportError:
    class config:
        AFFILIATE_TAG    = "dazzledeals00-20"
        SCRAPE_DELAY_MIN = 2.5
        SCRAPE_DELAY_MAX = 6.0

logger = logging.getLogger(__name__)
last_scrape_error: str = ""

# ─────────────────────────────────────────────
#  بروفايلات المتصفح
# ─────────────────────────────────────────────
BROWSER_PROFILES = [
    {
        "impersonate": "chrome124",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "platform": "Windows",
    },
    {
        "impersonate": "chrome123",
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
        "platform": "macOS",
    },
    {
        "impersonate": "chrome120",
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "platform": "Linux",
    },
    {
        "impersonate": "chrome116",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.188 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="116", "Not)A;Brand";v="24", "Google Chrome";v="116"',
        "platform": "Windows",
    },
]

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36"
)

AMAZON_DOMAINS = [
    "a.co", "amzn.to", "amzn.eu",
    "amazon.com", "amazon.fr", "amazon.co.uk", "amazon.de",
    "amazon.it", "amazon.es", "amazon.ca", "amazon.co.jp",
    "amazon.com.br", "amazon.com.tr", "amazon.ae", "amazon.com.au",
    "amazon.nl", "amazon.pl", "amazon.se", "amazon.com.mx",
    "amazon.in", "amazon.sg", "amazon.com.be", "amazon.com.sa",
]
SHORT_DOMAINS = {"a.co", "amzn.to", "amzn.eu"}

# ─────────────────────────────────────────────
#  Session management
# ─────────────────────────────────────────────
_PROFILE    = random.choice(BROWSER_PROFILES)
_SESSION    = None
_WARMED     = False
_COOKIE_JAR: dict = {}


def _build_session():
    global _SESSION
    if CURL_OK:
        _SESSION = curl_requests.Session(impersonate=_PROFILE["impersonate"])
    else:
        _SESSION = curl_requests.Session()
    _SESSION.headers.clear()


_build_session()


def _rotate_profile():
    global _PROFILE, _WARMED
    _PROFILE = random.choice(BROWSER_PROFILES)
    _build_session()
    _WARMED = False
    logger.info(f"[rotate] → {_PROFILE['impersonate']}")


# ─────────────────────────────────────────────
#  Headers
# ─────────────────────────────────────────────
def _desktop_headers(domain: str) -> dict:
    p    = _PROFILE
    lang = random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.9,en-US;q=0.8"])
    return {
        "User-Agent":                p["ua"],
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language":           lang,
        "Accept-Encoding":           "gzip, deflate, br",
        "Referer":                   f"https://{domain}/",
        "sec-ch-ua":                 p["sec_ch_ua"],
        "sec-ch-ua-mobile":          "?0",
        "sec-ch-ua-platform":        f'"{p["platform"]}"',
        "sec-fetch-dest":            "document",
        "sec-fetch-mode":            "navigate",
        "sec-fetch-site":            "same-origin",
        "sec-fetch-user":            "?1",
        "upgrade-insecure-requests": "1",
        "cache-control":             "max-age=0",
        "Connection":                "keep-alive",
        "DNT":                       "1",
    }


def _mobile_headers(domain: str) -> dict:
    return {
        "User-Agent":                MOBILE_UA,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "Referer":                   f"https://{domain}/",
        "sec-fetch-dest":            "document",
        "sec-fetch-mode":            "navigate",
        "sec-fetch-site":            "none",
        "sec-fetch-user":            "?1",
        "upgrade-insecure-requests": "1",
        "Connection":                "keep-alive",
    }


# ─────────────────────────────────────────────
#  Delays
# ─────────────────────────────────────────────
def _human_delay():
    lo  = float(getattr(config, "SCRAPE_DELAY_MIN", 2.5))
    hi  = float(getattr(config, "SCRAPE_DELAY_MAX", 6.0))
    base   = random.uniform(lo, hi)
    jitter = random.gauss(0, 0.3)
    time.sleep(max(0.8, base + jitter))


def _long_delay():
    wait = random.uniform(14, 28)
    logger.warning(f"[blocked] انتظار {wait:.1f}s…")
    time.sleep(wait)


# ─────────────────────────────────────────────
#  Warm-up
# ─────────────────────────────────────────────
def _warm(domain: str = "www.amazon.com"):
    global _WARMED
    if _WARMED:
        return
    try:
        r = _SESSION.get(
            f"https://{domain}/",
            headers=_desktop_headers(domain),
            timeout=15, allow_redirects=True,
        )
        _apply_cookies(r)
        time.sleep(random.uniform(2.0, 4.0))
        _SESSION.get(
            f"https://{domain}/bestsellers",
            headers=_desktop_headers(domain),
            timeout=15,
        )
        time.sleep(random.uniform(1.5, 3.0))
        _WARMED = True
        logger.info("[warm] ✅ اكتمل")
    except Exception as e:
        logger.warning(f"[warm] {e}")


def _apply_cookies(resp):
    try:
        for k, v in resp.cookies.items():
            _COOKIE_JAR[k] = v
    except Exception:
        pass


# ─────────────────────────────────────────────
#  URL helpers — FIX: www. دائماً
# ─────────────────────────────────────────────
def is_amazon_url(text: str) -> bool:
    return any(d in text.lower() for d in AMAZON_DOMAINS)


def _normalize_domain(host: str) -> str:
    """يضيف www. تلقائياً لأي نطاق Amazon يفتقده."""
    host = host.lower().lstrip(".")
    if host.startswith("www."):
        return host
    if host in SHORT_DOMAINS:
        return host
    for d in AMAZON_DOMAINS:
        if host == d or host.endswith("." + d):
            return "www." + host
    return host


def get_amazon_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc or ""
        return _normalize_domain(host) or "www.amazon.com"
    except Exception:
        return "www.amazon.com"


def expand_url(url: str) -> str:
    if not url.startswith("http"):
        url = "https://" + url
    if not any(d in url.lower() for d in SHORT_DOMAINS):
        return url
    for attempt in range(4):
        try:
            r = _SESSION.head(
                url,
                headers={"User-Agent": _PROFILE["ua"]},
                allow_redirects=True, timeout=15,
            )
            final = r.url or url
            if final != url:
                return final
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    try:
        return _SESSION.get(url, allow_redirects=True, timeout=20).url or url
    except Exception:
        return url


def _normalize_price_to_usd(price_str: str) -> str:
    """Force any price string to USD format ($XX.XX)."""
    if not price_str:
        return ""
    import re
    nums = re.findall(r"[\d.]+", price_str)
    if nums:
        return f"${nums[0]}"
    return ""


def extract_asin(url: str) -> str | None:
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/gp/aw/d/([A-Z0-9]{10})",
        r"/ASIN/([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
        r"[?&]asin=([A-Z0-9]{10})",
        r"/exec/obidos/ASIN/([A-Z0-9]{10})",
        r"/d/([A-Z0-9]{10})",
        r"-(B[A-Z0-9]{9})(?:[/?#]|$)",
    ]
    for pat in patterns:
        m = re.search(pat, url, re.IGNORECASE)
        if m:
            c = m.group(1).upper()
            if len(c) == 10:
                return c
    return None


def is_store_or_brand_url(url: str) -> bool:
    """Detect Amazon Store/Brand/Category pages (not product pages)."""
    store_patterns = [
        r"/stores/",
        r"/brand/",
        r"/b/",
        r"/shops/",
        r"/store/",
        r"/s\?k=",  # search pages
        r"/gp/browse",
        r"/Deal ",
        r"/b\?",
        r"/stores/page/",
    ]
    url_lower = url.lower()
    return any(re.search(p, url_lower) for p in store_patterns)


def _extract_products_from_store(url: str, max_products: int = 5) -> list[str]:
    """Extract product ASINs from Amazon Store/Brand pages."""
    domain = get_amazon_domain(url)
    soup = _fetch(url, retries=3)
    if not soup:
        return []

    asins = []
    seen = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        asin = extract_asin(href)
        if asin and asin not in seen:
            seen.add(asin)
            asins.append(f"https://{domain}/dp/{asin}")
            if len(asins) >= max_products:
                break

    logger.info(f"[store] Extracted {len(asins)} products from store page")
    return asins


def _fetch_via_jina(url: str) -> dict | None:
    """Fallback: Use Jina AI Reader (free) to extract content."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            "Accept": "text/plain",
            "X-Return-Format": "text",
        }
        r = curl_requests.get(jina_url, headers=headers, timeout=30)
        if r.status_code == 200:
            text = r.text
            title_match = re.search(r"Title:\s*(.+)", text)
            price_match = re.search(r"\$[\d,.]+", text)
            asin_match = re.search(r"ASIN:\s*([A-Z0-9]{10})", text, re.IGNORECASE)

            title = title_match.group(1).strip() if title_match else ""
            price = price_match.group(0) if price_match else ""
            asin = asin_match.group(1).upper() if asin_match else extract_asin(url)

            if title and asin:
                logger.info(f"[jina] ✅ Extracted: {title[:50]}")
                return {
                    "asin": asin,
                    "title": title,
                    "price": price,
                    "clean_url": f"https://www.amazon.com/dp/{asin}",
                    "aff_link": build_affiliate_url(f"https://www.amazon.com/dp/{asin}"),
                    "img_url": "",
                    "brand": "",
                    "original_price": "",
                    "rating": 0,
                    "review_count": 0,
                    "coupon": "",
                    "category": "",
                    "availability": "",
                    "features": [],
                    "price_numeric": _parse_float(price),
                }
    except Exception as e:
        logger.warning(f"[jina] Fallback failed: {e}")
    return None


def _fetch_via_apify(url: str) -> dict | None:
    """Fallback: Use Apify scraper (free tier available)."""
    try:
        import httpx
        asin = extract_asin(url)
        if not asin:
            return None

        api_url = "https://api.apify.com/v2/acts/jupri~amazon-product-scraper/runs"
        payload = {"urls": [f"https://www.amazon.com/dp/{asin}"]}
        r = httpx.post(api_url, json=payload, timeout=30)
        if r.status_code in (200, 201):
            data = r.json()
            items = data.get("data", {}).get("items", [])
            if items:
                item = items[0]
                logger.info(f"[apify] ✅ Extracted: {item.get('title', '')[:50]}")
                return {
                    "asin": asin,
                    "title": item.get("title", ""),
                    "price": item.get("price", ""),
                    "clean_url": f"https://www.amazon.com/dp/{asin}",
                    "aff_link": build_affiliate_url(f"https://www.amazon.com/dp/{asin}"),
                    "img_url": item.get("image", ""),
                    "brand": item.get("brand", ""),
                    "original_price": "",
                    "rating": float(item.get("rating", 0) or 0),
                    "review_count": int(item.get("reviewCount", 0) or 0),
                    "coupon": "",
                    "category": "",
                    "availability": item.get("availability", ""),
                    "features": item.get("features", []),
                    "price_numeric": _parse_float(item.get("price", "")),
                }
    except Exception as e:
        logger.warning(f"[apify] Fallback failed: {e}")
    return None


def build_clean_url(url: str) -> str:
    url    = expand_url(url)
    asin   = extract_asin(url)
    domain = get_amazon_domain(url)
    return f"https://{domain}/dp/{asin}" if asin else url.split("?")[0]


def build_affiliate_url(clean_url: str) -> str:
    tag = getattr(config, "AFFILIATE_TAG", "dazzledeals00-20")
    sep = "&" if "?" in clean_url else "?"
    return f"{clean_url}{sep}tag={tag}"


# ─────────────────────────────────────────────
#  Block detection
# ─────────────────────────────────────────────
BLOCK_TEXTS = [
    "captcha", "robot check", "enter the characters",
    "automated access", "sorry, we just need to make sure",
    "type the characters you see", "are you a human",
    "verify your identity", "unusual traffic", "access denied",
]


def _blocked(soup: BeautifulSoup, url: str) -> bool:
    url_low = url.lower()
    text    = soup.get_text(" ", strip=True).lower()
    if "ap/signin" in url_low or "ap/cvf" in url_low:
        return True
    if len(text) < 600:
        return True
    if any(b in text for b in BLOCK_TEXTS):
        return True
    title = soup.find("title")
    if title:
        t = title.get_text().lower()
        if any(w in t for w in ("robot", "captcha", "sorry", "blocked")):
            return True
    return False


# ─────────────────────────────────────────────
#  EXTRACTOR — Title
# ─────────────────────────────────────────────
def _extract_title(soup: BeautifulSoup) -> str:
    for sel in [
        "#productTitle", "span#productTitle",
        "h1#title span", "#title_feature_div h1",
        "#ebooksProductTitle", "h1.a-size-large",
        "#titleSection h1", "#gc-asin-title",
    ]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if len(t) > 5:
                return re.sub(r"\s+", " ", t).strip()

    tag = soup.find("title")
    if tag:
        t = tag.get_text(strip=True)
        t = re.sub(r"(?i)\s*[:\-|]\s*Amazon\.?\w*$", "", t).strip()
        t = re.sub(r"(?i)^Amazon\.?\w*\s*[:\-|]?\s*", "", t).strip()
        if len(t) > 8:
            return t
    return ""


# ─────────────────────────────────────────────
#  EXTRACTOR — Price  ← إصلاح رئيسي
# ─────────────────────────────────────────────
def _extract_price(soup: BeautifulSoup) -> str:

    # ① JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data   = json.loads(script.string or "")
            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = offers.get("price") or offers.get("lowPrice")
            if price:
                return f"${price}"
        except Exception:
            pass

    # ② JS inline data
    for script in soup.find_all("script"):
        txt = script.string or ""
        for pattern in (r'"priceAmount"\s*:\s*([\d.]+)',
                        r'"buyingPrice"\s*:\s*([\d.]+)',
                        r'"price"\s*:\s*"([\d.]+)"'):
            m = re.search(pattern, txt)
            if m:
                return f"${m.group(1)}"

    # ③ CSS selectors — من الأدق للأعم
    selectors = [
        ".priceToPay span.a-offscreen",
        "#corePrice_feature_div .a-offscreen",
        "#corePrice_desktop .a-offscreen",
        "#apex_offerDisplay_desktop .a-offscreen",
        "span.a-price[data-a-color='price'] .a-offscreen",
        "#price_feature_div .a-offscreen",
        "#newBuyBoxPrice",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "span#price_inside_buybox",
        ".apexPriceToPay .a-offscreen",
        "#kindle-price",
        ".kindle-price .a-color-price",
        "#tmmSwatches .selected .a-color-price",
        "#usedBuySection .a-color-price",
        "#price",
        ".a-price .a-offscreen",
    ]
    seen = set()
    for sel in selectors:
        for el in soup.select(sel):
            p = el.get_text(strip=True)
            if p in seen:
                continue
            seen.add(p)
            if any(c.isdigit() for c in p) and len(p) <= 25:
                return _normalize_price_to_usd(p)

    # ④ أي عنصر يحمل class تحتوي price
    for el in soup.find_all(class_=re.compile(r"price", re.I)):
        t = el.get_text(strip=True)
        if re.match(r'^[\$£€¥₹]?\s*\d[\d,\.]*$', t):
            return _normalize_price_to_usd(t)

    return "N/A"


def _parse_float(s: str) -> float:
    try:
        cleaned = re.sub(r"[^\d.]", "", s)
        return float(cleaned) if cleaned else 9999.0
    except Exception:
        return 9999.0


# ─────────────────────────────────────────────
#  EXTRACTOR — Rating & Reviews
# ─────────────────────────────────────────────
def _extract_rating(soup: BeautifulSoup) -> float:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            agg  = data.get("aggregateRating", {})
            rv   = agg.get("ratingValue")
            if rv:
                return round(float(rv), 1)
        except Exception:
            pass
    for sel in [
        "span[data-hook='rating-out-of-text']",
        "#acrPopover span.a-icon-alt",
        "i[data-hook='average-star-rating'] span.a-icon-alt",
        "#averageCustomerReviews span.a-icon-alt",
    ]:
        el = soup.select_one(sel)
        if el:
            m = re.search(r"([\d.]+)", el.get_text())
            if m:
                return round(float(m.group(1)), 1)
    return 0.0


def _extract_reviews(soup: BeautifulSoup) -> int:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            agg  = data.get("aggregateRating", {})
            rc   = agg.get("reviewCount") or agg.get("ratingCount")
            if rc:
                return int(rc)
        except Exception:
            pass
    for sel in [
        "#acrCustomerReviewText",
        "span[data-hook='total-review-count']",
    ]:
        el = soup.select_one(sel)
        if el:
            m = re.search(r"([\d,]+)", el.get_text())
            if m:
                return int(m.group(1).replace(",", ""))
    return 0


# ─────────────────────────────────────────────
#  EXTRACTOR — Image  ← إصلاح رئيسي
# ─────────────────────────────────────────────
def _upgrade_image(url: str) -> str:
    if not url:
        return url
    url = re.sub(r"\._[A-Z]{2}\d+_\.", "._SL1500_.", url)
    url = re.sub(r"\._[A-Z]+,.*?\.", ".", url)
    return url


def _img_score(url: str) -> int:
    if not url or "media-amazon.com/images" not in url:
        return -1
    if any(x in url for x in ("sprite", "icon", "pixel", "gif", "transparent")):
        return -1
    score = 10
    m = re.search(r"_SL(\d+)_", url)
    if m:
        score += int(m.group(1))
    if "_AC_" in url:
        score += 200
    if "images/I/" in url:
        score += 100
    return score


def _extract_image(soup: BeautifulSoup) -> str:
    candidates: list[tuple[int, str]] = []

    # ① data-a-dynamic-image (أفضل مصدر)
    for img_id in ("landingImage", "imgBlkFront", "main-image",
                   "img-canvas", "imageBlock_feature_div"):
        tag = soup.find(id=img_id)
        if not tag:
            continue
        dyn = tag.get("data-a-dynamic-image", "")
        if dyn:
            try:
                for u, dims in json.loads(dyn).items():
                    w = dims[0] if dims else 0
                    candidates.append((w + 500, u))
            except Exception:
                pass
        for attr in ("data-old-hires", "data-src", "src"):
            u = tag.get(attr, "")
            s = _img_score(u)
            if s > 0:
                candidates.append((s, u))

    # ② حاويات الصور البديلة
    for cid in ("imageBlockThumbs", "altImages", "imageBlock",
                "imageContainer", "main-image-container"):
        c = soup.find(id=cid)
        if c:
            for img in c.find_all("img"):
                for attr in ("data-old-hires", "data-src", "src"):
                    u = img.get(attr, "")
                    s = _img_score(u)
                    if s > 0:
                        candidates.append((s, u))

    # ③ كل الصور في الصفحة
    for img in soup.find_all("img"):
        for attr in ("data-old-hires", "data-src", "src"):
            u = img.get(attr, "")
            s = _img_score(u)
            if s > 0:
                candidates.append((s, u))

    # ④ روابط الصور داخل JavaScript
    for script in soup.find_all("script"):
        txt = script.string or ""
        for m in re.finditer(
            r'(https://m\.media-amazon\.com/images/[^\s\'"\\]+)', txt
        ):
            u = m.group(1)
            s = _img_score(u)
            if s > 0:
                candidates.append((s, u))

    if not candidates:
        return ""

    best = sorted(candidates, key=lambda x: x[0], reverse=True)[0][1]
    return _upgrade_image(best)


def _extract_all_images(soup: BeautifulSoup) -> list[str]:
    """Extract 2-3 high-quality product images from Amazon page."""
    seen = set()
    images = []

    # ① data-a-dynamic-image (best source - multiple resolutions)
    for img_id in ("landingImage", "imgBlkFront", "main-image",
                   "img-canvas", "imageBlock_feature_div"):
        tag = soup.find(id=img_id)
        if not tag:
            continue
        dyn = tag.get("data-a-dynamic-image", "")
        if dyn:
            try:
                for u, dims in json.loads(dyn).items():
                    w = dims[0] if dims else 0
                    if w >= 400 and u not in seen:
                        seen.add(u)
                        images.append(_upgrade_image(u))
            except Exception:
                pass
        for attr in ("data-old-hires", "data-src", "src"):
            u = tag.get(attr, "")
            if u and "media-amazon" in u and u not in seen:
                s = _img_score(u)
                if s > 0:
                    seen.add(u)
                    images.append(_upgrade_image(u))

    # ② Thumbnail images (altImages container)
    for cid in ("imageBlockThumbs", "altImages", "imageBlock",
                "imageContainer", "main-image-container"):
        c = soup.find(id=cid)
        if c:
            for img in c.find_all("img"):
                for attr in ("data-old-hires", "data-src", "src"):
                    u = img.get(attr, "")
                    if u and "media-amazon" in u and u not in seen:
                        s = _img_score(u)
                        if s > 0:
                            seen.add(u)
                            images.append(_upgrade_image(u))

    # ③ All images on page (fallback for more variety)
    for img in soup.find_all("img"):
        for attr in ("data-old-hires", "data-src", "src"):
            u = img.get(attr, "")
            if u and "media-amazon" in u and u not in seen:
                s = _img_score(u)
                if s > 50:  # Higher threshold for fallback
                    seen.add(u)
                    images.append(_upgrade_image(u))

    # Return top 3 unique images
    return images[:3] if images else []


def _extract_customer_reviews(soup: BeautifulSoup) -> list[dict]:
    """Extract actual customer reviews (4+ stars only) from Amazon page."""
    reviews = []

    # Method 1: Look for review elements in the page
    for sel in [
        "[data-hook='review']",
        ".review",
        ".a-section.review",
    ]:
        for el in soup.select(sel)[:10]:  # Check first 10 reviews
            try:
                # Extract star rating
                star_el = el.select_one("[data-hook='review-star-rating'] span, .a-icon-alt, [data-hook='cmps-review-star-rating'] span")
                if not star_el:
                    continue
                star_text = star_el.get_text(strip=True)
                star_match = re.search(r'(\d\.?\d?)', star_text)
                if not star_match:
                    continue
                stars = float(star_match.group(1))
                if stars < 4.0:
                    continue  # Skip reviews below 4 stars

                # Extract review title
                title_el = el.select_one("[data-hook='review-title'] span, .review-title span, a[data-hook='review-title']")
                title = title_el.get_text(strip=True) if title_el else ""

                # Extract review body
                body_el = el.select_one("[data-hook='review-body'] span, .review-text span, .review-text-content span")
                body = body_el.get_text(strip=True)[:300] if body_el else ""

                # Extract reviewer name
                name_el = el.select_one(".a-profile-name, [data-hook='review-author']")
                name = name_el.get_text(strip=True) if name_el else "Customer"

                if title or body:
                    reviews.append({
                        "stars": stars,
                        "title": title,
                        "body": body,
                        "name": name,
                    })
            except Exception:
                continue

    # Method 2: Look in JSON-LD for review data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "Review":
                        rating = item.get("reviewRating", {}).get("ratingValue", 0)
                        if float(rating) >= 4.0:
                            reviews.append({
                                "stars": float(rating),
                                "title": item.get("name", ""),
                                "body": item.get("reviewBody", "")[:300],
                                "name": item.get("author", {}).get("name", "Customer"),
                            })
        except Exception:
            pass

    return reviews[:5]  # Return top 5 reviews (4+ stars only)


# ─────────────────────────────────────────────
#  EXTRACTOR — misc
# ─────────────────────────────────────────────
def _extract_coupon(soup: BeautifulSoup) -> str:
    for sel in (".couponBadge", "#couponText", "[data-hook='coupon-label']",
                ".promoPriceBlockMessage", "#dealBadgeSupportingText"):
        el = soup.select_one(sel)
        if el:
            return el.get_text(" ", strip=True)
    return ""


def _extract_category(soup: BeautifulSoup) -> str:
    el = soup.select_one("#wayfinding-breadcrumbs_feature_div")
    if el:
        items = [i.get_text(strip=True) for i in el.find_all("li") if i.get_text(strip=True)]
        return items[0] if items else ""
    return ""


def _extract_brand(soup: BeautifulSoup) -> str:
    for sel in ("#bylineInfo", ".po-brand td.a-span9", "#brand", "a#bylineInfo"):
        el = soup.select_one(sel)
        if el:
            t = re.sub(r"(?i)^(visit the |brand:\s*)", "",
                       el.get_text(strip=True)).strip()
            if t:
                return t
    return ""


def _extract_availability(soup: BeautifulSoup) -> str:
    if soup.select_one("#add-to-cart-button") or soup.select_one("#buy-now-button"):
        return "In Stock"
    el = soup.select_one("#availability span, #outOfStock span")
    if el:
        t = el.get_text(strip=True).lower()
        if "in stock" in t:
            return "In Stock"
        if "out of stock" in t or "unavailable" in t:
            return "Out of Stock"
        if t:
            return t.capitalize()
    return "Unknown"


def _extract_features(soup: BeautifulSoup) -> list[str]:
    el = soup.select_one("#feature-bullets ul, #featurebullets_feature_div ul")
    if not el:
        return []
    items = []
    for li in el.find_all("li"):
        t = li.get_text(" ", strip=True)
        if t and "Make sure this fits" not in t:
            items.append(t)
    return items[:6]


def _extract_original_price(soup: BeautifulSoup) -> str:
    for sel in (".basisPrice span.a-offscreen",
                "span.a-price.a-text-price span.a-offscreen",
                "#listPrice", "#priceblock_listprice"):
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if any(c.isdigit() for c in t):
                return _normalize_price_to_usd(t)
    return ""


# ─────────────────────────────────────────────
#  Validation — الصورة اختيارية
# ─────────────────────────────────────────────
def is_valid_product(p: dict) -> bool:
    if not p:
        return False
    title = p.get("title", "").strip()
    price = p.get("price", "").strip()
    if len(title) < 8:
        return False
    if not price or price.lower() in ("n/a", "none", ""):
        return False
    if not any(c.isdigit() for c in price):
        return False
    return True


# ─────────────────────────────────────────────
#  FETCH
# ─────────────────────────────────────────────
def _fetch(url: str, *, mobile: bool = False, retries: int = 3) -> BeautifulSoup | None:
    global last_scrape_error, _WARMED

    domain = get_amazon_domain(url)

    for attempt in range(1, retries + 1):
        _human_delay()
        try:
            headers = _mobile_headers(domain) if mobile else _desktop_headers(domain)

            if CURL_OK and not mobile:
                _SESSION.impersonate = _PROFILE["impersonate"]

            resp = _SESSION.get(
                url, headers=headers, cookies=_COOKIE_JAR,
                timeout=30, allow_redirects=True,
            )
            _apply_cookies(resp)

            if resp.status_code in (429, 503, 403):
                last_scrape_error = f"HTTP {resp.status_code}"
                logger.warning(f"[fetch] {resp.status_code} — محاولة {attempt}/{retries}")
                if attempt < retries:
                    _long_delay()
                    _rotate_profile()
                continue

            if resp.status_code != 200:
                last_scrape_error = f"HTTP {resp.status_code}"
                return None

            content = resp.content
            try:
                if resp.headers.get("Content-Encoding") == "gzip":
                    content = gzip.decompress(content)
            except Exception:
                pass

            soup = BeautifulSoup(content, "html.parser")

            if _blocked(soup, resp.url):
                last_scrape_error = "CAPTCHA detected"
                logger.warning(f"[fetch] محظور — محاولة {attempt}/{retries}")
                if attempt < retries:
                    _long_delay()
                    _rotate_profile()
                    _WARMED = False
                    _warm(domain)
                continue

            logger.info(f"[fetch] ✅ {url[:70]} — {len(content)} bytes")
            return soup

        except Exception as e:
            last_scrape_error = str(e)
            logger.error(f"[fetch] {e} — محاولة {attempt}/{retries}")
            if attempt < retries:
                time.sleep(random.uniform(3, 8))

    return None


# ─────────────────────────────────────────────
#  SCRAPE PRODUCT (Multi-layer fallback)
# ─────────────────────────────────────────────
def scrape_product(url: str) -> dict | None:
    global last_scrape_error

    # Step 0: If it's a store/brand page, extract product links first
    if is_store_or_brand_url(url):
        logger.info("[scrape] Store/Brand page detected — extracting products...")
        product_urls = _extract_products_from_store(url)
        if product_urls:
            url = product_urls[0]  # Take first product
            logger.info(f"[scrape] Using first product: {url}")
        else:
            last_scrape_error = "لم يتم العثور على منتجات في صفحة المتجر"
            return None

    clean  = build_clean_url(url)
    asin   = extract_asin(clean)
    if not asin:
        last_scrape_error = "تعذّر استخراج ASIN"
        return None

    domain = get_amazon_domain(clean)
    base   = f"https://{domain}/dp/{asin}"

    # Layer 1: Desktop fetch
    soup = _fetch(base, retries=3)

    # Layer 2: Mobile fetch
    if not soup:
        logger.info("[scrape] محاولة Mobile…")
        soup = _fetch(f"https://{domain}/dp/{asin}?m=1", mobile=True, retries=2)

    # Layer 3: AW page (lightweight)
    if not soup:
        logger.info("[scrape] محاولة /gp/aw/d/…")
        soup = _fetch(f"https://{domain}/gp/aw/d/{asin}", mobile=True, retries=2)

    # Layer 4: Jina AI Reader (free fallback)
    if not soup:
        logger.info("[scrape] محاولة Jina AI Reader…")
        jina_result = _fetch_via_jina(f"https://{domain}/dp/{asin}")
        if jina_result:
            return jina_result

    # Layer 5: Apify scraper (free tier)
    if not soup:
        logger.info("[scrape] محاولة Apify scraper…")
        apify_result = _fetch_via_apify(f"https://{domain}/dp/{asin}")
        if apify_result:
            return apify_result

    if not soup:
        last_scrape_error = "فشل الاتصال بخوادم Amazon (جميع المحاولات فشلت)"
        return None

    title = _extract_title(soup)
    if not title:
        last_scrape_error = "لم يُعثر على عنوان المنتج"
        return None

    price = _extract_price(soup)
    img   = _extract_image(soup)
    all_imgs = _extract_all_images(soup)
    orig  = _extract_original_price(soup)
    customer_reviews = _extract_customer_reviews(soup)

    product = {
        "asin":           asin,
        "title":          title,
        "brand":          _extract_brand(soup),
        "price":          price,
        "price_numeric":  _parse_float(price),
        "original_price": orig,
        "rating":         _extract_rating(soup),
        "review_count":   _extract_reviews(soup),
        "img_url":        img,
        "all_images":     all_imgs if all_imgs else ([img] if img else []),
        "customer_reviews": customer_reviews,
        "clean_url":      base,
        "aff_link":       build_affiliate_url(base),
        "coupon":         _extract_coupon(soup),
        "category":       _extract_category(soup),
        "availability":   _extract_availability(soup),
        "features":       _extract_features(soup),
    }

    if not is_valid_product(product):
        last_scrape_error = "بيانات المنتج غير مكتملة (لا عنوان أو سعر)"
        return None

    return product


# ─────────────────────────────────────────────
#  SEARCH
# ─────────────────────────────────────────────
def search_amazon(
    keyword: str,
    max_results: int = 5,
    domain: str = "www.amazon.com",
    min_rating: float = 4.0,
    min_reviews: int = 50,
) -> list[dict]:
    _warm(domain)

    soup = _fetch(f"https://{domain}/s?k={quote(keyword)}", retries=3)
    if not soup:
        return []

    asins_seen: set = set()
    links: list     = []

    for tag in soup.select(
        "a.a-link-normal.s-no-outline, h2 a.a-link-normal, "
        "a[data-component-type='s-product-image'], .s-result-item h2 a"
    ):
        href = tag.get("href", "")
        asin = extract_asin(href)
        if asin and asin not in asins_seen:
            asins_seen.add(asin)
            links.append(f"https://{domain}/dp/{asin}")

    logger.info(f"[search] {len(links)} نتيجة لـ '{keyword}'")

    results = []
    for link in links[: max_results * 4]:
        p = scrape_product(link)
        if not p or not is_valid_product(p):
            continue
        if p["rating"] < min_rating or p["review_count"] < min_reviews:
            continue
        pv = p["price_numeric"] if p["price_numeric"] > 0 else 9999
        p["value_score"] = round(p["rating"] / pv * 1000, 4)
        results.append(p)
        if len(results) >= max_results:
            break

    results.sort(key=lambda x: x.get("value_score", 0), reverse=True)
    return results


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys, pprint
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.amazon.com/dp/B07D5M7SNW"
    print(f"\n🔍 جاري سحب: {url}\n")

    result = scrape_product(url)

    if result:
        print("✅ نجح السحب!\n")
        pprint.pprint({k: v for k, v in result.items() if k != "features"})
        if result.get("features"):
            print("\n📋 المميزات:")
            for f in result["features"]:
                print(f"  • {f}")
    else:
        print(f"❌ فشل: {last_scrape_error}")
