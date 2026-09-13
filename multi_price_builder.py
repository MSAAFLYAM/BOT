"""
multi_price_builder.py
Builds multi-platform SEARCH links (no price scraping — to avoid Amazon CAPTCHA).
"""
from urllib.parse import quote

AFFILIATE_TAG = "dazzledeals00-20"


def build_multi_platform_links(product_title: str, asin: str) -> list[dict]:
    """
    Returns a list of platforms with ready search links.
    Does NOT scrape prices — shows a "Check Price" button per platform.

    Returns:
    [
        {
            "platform": "Amazon US",
            "flag": "",
            "url": "https://amazon.com/dp/B09DT48V16?tag=...",
            "price": None,
            "trust_badge": "Trusted",
            "is_affiliate": True,
            "note": "Free shipping with Prime",
            "primary": True
        },
        ...
    ]
    """
    encoded = quote(product_title[:80])

    platforms = [
        {
            "platform": "Amazon US",
            "flag": "🇺🇸",
            "url": f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}" if asin else f"https://www.amazon.com/s?k={encoded}&tag={AFFILIATE_TAG}",
            "price": None,  # filled from the main scraper
            "trust_badge": "✅ Trusted",
            "is_affiliate": True,
            "note": "Free shipping with Prime",
            "primary": True
        },
        {
            "platform": "Amazon UK",
            "flag": "🇬🇧",
            "url": f"https://www.amazon.co.uk/s?k={encoded}",
            "price": None,
            "trust_badge": "✅ Trusted",
            "is_affiliate": False,
            "note": "Prices in GBP"
        },
        {
            "platform": "Amazon DE",
            "flag": "🇩🇪",
            "url": f"https://www.amazon.de/s?k={encoded}",
            "price": None,
            "trust_badge": "✅ Trusted",
            "is_affiliate": False,
            "note": "Prices in EUR"
        },
        {
            "platform": "eBay",
            "flag": "🛒",
            "url": f"https://www.ebay.com/sch/i.html?_nkw={encoded}&_sop=12",
            "price": None,
            "trust_badge": "⚠️ Verify seller",
            "is_affiliate": False,
            "note": "Check seller ratings"
        },
        {
            "platform": "AliExpress",
            "flag": "🌏",
            "url": f"https://www.aliexpress.com/wholesale?SearchText={encoded}",
            "price": None,
            "trust_badge": "⚠️ Longer shipping",
            "is_affiliate": False,
            "note": "Cheaper but slower delivery"
        }
    ]

    return platforms


def try_scrape_amazon_uk_price(asin: str) -> str | None:
    """
    Optional attempt to scrape the Amazon UK price for the primary ASIN.
    If it fails (CAPTCHA/block): returns None without crashing.
    """
    if not asin:
        return None
    try:
        import requests, re
        url = f"https://r.jina.ai/https://www.amazon.co.uk/dp/{asin}"
        r = requests.get(url, timeout=10, headers={"Accept": "text/plain"})
        if r.status_code == 200:
            # look for a GBP price pattern
            match = re.search(r'£\s*([\d,]+\.?\d*)', r.text)
            if match:
                return f"£{match.group(1)}"
    except Exception as e:
        print(f"[MULTI_PRICE] UK price scrape failed: {e}")
    return None