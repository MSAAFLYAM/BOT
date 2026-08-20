# blogger_api_publisher.py — Blogger API v3 (NO SMTP, NO EMAIL)
# ✅ Uses existing env vars: BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET,
#    BLOGGER_REFRESH_TOKEN, BLOGGER_BLOG_ID
# ✅ Works 100% — no SMTP port blocking
# ✅ Returns real post URL + post ID
# ✅ Supports labels, draft vs publish, HTML content
# ✅ Automatically alerts Telegram channel with a premium layout

import os
import logging
import requests
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# Set to True during bulk fix to skip Telegram alerts
SKIP_TELEGRAM_ALERT = False

# ── Image transformation ─────────────────────────────────────────────────────
try:
    from image_transformer import transform_image
    from image_processor import upload_image as _upload_image_to_host
    IMAGE_TRANSFORM_ENABLED = True
    logger.info("[publisher] Image transformation enabled")
except ImportError:
    IMAGE_TRANSFORM_ENABLED = False
    logger.warning("[publisher] Image transformation disabled (modules not found)")

# ── ENV VARIABLES ─────────────────────────────────────────────────────────────
CLIENT_ID     = os.environ.get("BLOGGER_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN", "").strip()
BLOG_ID       = os.environ.get("BLOGGER_BLOG_ID", "").strip()

TOKEN_URL     = "https://oauth2.googleapis.com/token"
BLOGGER_BASE  = "https://www.googleapis.com/blogger/v3"


# ── TELEGRAM NOTIFIER HELPER ──────────────────────────────────────────────────

def _send_telegram_alert(title: str, price: str, post_url: str):
    """Sends a clean notification to your Telegram channel using the shared bot."""
    if SKIP_TELEGRAM_ALERT:
        return
    # We import inside to avoid circular dependency loops at boot time
    try:
        from main import bot
        import config
        
        channel_id = config.CHANNEL_ID or "@promoparad"
        
        # Build an eye-catching message for mobile users
        tg_text = (
            f"📢 <b>New Tech Review Online!</b>\n\n"
            f"🛒 <b>Product:</b> {title[:75]}...\n"
            f"💰 <b>Best Price:</b> {price}\n\n"
            f"🔍 Read our full unbiased analysis, pros & cons, and verdict on our blog!"
        )
        
        # Custom button linking to the newly created Blogger post URL
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("👉 Read Full Review Here", url=post_url))
        
        bot.send_message(channel_id, tg_text, parse_mode="HTML", reply_markup=markup)
        logger.info(f"[blogger_api] ✈️ Telegram channel alerted successfully.")
    except Exception as e:
        logger.error(f"[blogger_api] ❌ Failed to send Telegram alert: {e}")


# ── AUTH ───────────────────────────────────────────────────────────────────────

def _get_access_token() -> str:
    """Exchange refresh token → fresh access token (valid 1h)."""
    resp = requests.post(TOKEN_URL, data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type":    "refresh_token",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Token refresh failed: {data}")
    return data["access_token"]


def _map_labels(title: str, existing_labels: list | None = None) -> list:
    """Map product title to Blogger labels (canonical list)."""
    t = title.lower()

    if any(k in t for k in ["makeup","skincare","serum","beauty","cosmetic","lip","face cream","moisturizer","foundation","mascara","lipstick","sunscreen","cleanser"]):
        return ["Beauty & Skincare", "Deals"]
    if any(k in t for k in ["kitchen","cook","pot","pan","blender","coffee","fryer","knife","spatula","bakery","oven","microwave","air fryer","toaster","mixer"]):
        return ["Kitchen & Home", "Deals"]
    if any(k in t for k in ["bed","chair","rug","lamp","decor","pillow","blanket","curtain","shelf","table","sofa","desk","fan","heater","vacuum","mattress"]):
        return ["Home & Living", "Deals"]
    if any(k in t for k in ["dog","cat","pet","puppy","kitten","fish","bird","hamster","rabbit","reptile"]):
        return ["Pet Products", "Deals"]
    if any(k in t for k in ["shoe","sneaker","boot","sandal","dress","shirt","jean","jacket","hat","watch","jewelry","clothing","fashion","wallet","bag","backpack","sunglasses"]):
        return ["Fashion & Accessories", "Deals"]
    if any(k in t for k in ["headphone","earbuds","speaker","phone","tablet","laptop","computer","monitor","keyboard","mouse","cable","charger","camera","gadget","tech","smart","bluetooth","wireless","usb","hdmi","ssd","hard drive","mouse","router","power bank"]):
        return ["Electronics & Gadgets", "Deals"]
    if any(k in t for k in ["dumbbell","weight","gym","yoga","fitness","sport","workout","exercise","running","bicycle","bike","treadmill","resistance","foam roller"]):
        return ["Sports & Fitness", "Deals"]
    if any(k in t for k in ["toy","game","puzzle","lego","doll","car toy","kids","baby","infant","stroller","car seat","diaper"]):
        return ["Toys & Kids", "Deals"]
    if any(k in t for k in ["book","notebook","pen","pencil","office","desk organizer","file","binder"]):
        return ["Books & Office", "Deals"]
    if any(k in t for k in ["car","auto","motorcycle","tire","wiper","seat cover","mirror","dashcam","gps"]):
        return ["Automotive", "Deals"]
    return ["Deals"]


def is_configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN and BLOG_ID)


def test_connection() -> bool:
    if not is_configured():
        return False
    try:
        token = _get_access_token()
        resp  = requests.get(
            f"{BLOGGER_BASE}/blogs/{BLOG_ID}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"[blogger_api] test_connection failed: {e}")
        return False


# ── BUILD HTML ARTICLE ─────────────────────────────────────────────────────────

def _ai_generate_content(title: str, price: str, description: str) -> dict:
    """Generate product-specific content via AI (Groq/OpenRouter) with fallback."""
    import os, httpx, json

    short_title = title[:80]
    prompt = (
        f"You are an Amazon affiliate reviewer. Write for:\n"
        f"Title: {short_title}\nPrice: {price}\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"intro": "2 sentences about the product",\n'
        f' "why_like": "1 paragraph why buyers like it",\n'
        f' "best_for": ["5 use cases"],\n'
        f' "pros": ["4 pros"],\n'
        f' "cons": ["2 cons"],\n'
        f' "specs": {{"Brand": "...", "Model": "...", "Weight": "..."}},\n'
        f' "verdict": "1 sentence verdict",\n'
        f' "faq": [{{"q": "question", "a": "answer"}} for 2 questions],\n'
        f' "final": "1 sentence CTA"}}\n'
        f"Rules: Be specific. Short answers. No markdown."
    )

    groq_key = os.environ.get("GROQ_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    result = {}

    if groq_key:
        try:
            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={"model": "qwen/qwen3.6-27b", "messages": [{"role": "user", "content": prompt}], "max_tokens": 1200, "temperature": 0.3},
                timeout=25,
            )
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                # Strip <think>...</think> tags (Qwen model thinking)
                text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
                # Also strip any <think>...</think> that might not have closing tag
                text = re.sub(r"<think>[\s\S]*$", "", text).strip()
                text = re.sub(r"^```json\n?|\n?```$", "", text).strip()
                if text:
                    result = json.loads(text)
        except Exception as e:
            logger.warning(f"[ai_content] Groq failed: {e}")

    if not result and openrouter_key:
        try:
            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {openrouter_key}", "Content-Type": "application/json"},
                json={"model": "google/gemma-3-27b-it:free", "messages": [{"role": "user", "content": prompt}], "max_tokens": 800, "temperature": 0.7},
                timeout=25,
            )
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                text = re.sub(r"^```json\n?|\n?```$", "", text)
                result = json.loads(text)
        except Exception as e:
            logger.warning(f"[ai_content] OpenRouter failed: {e}")

    if not result:
        logger.info("[ai_content] Fallback to template content")
        result = {
            "intro": f"The {short_title} is a top-rated product on Amazon, combining quality craftsmanship with excellent value. Thousands of satisfied customers have made it a bestseller in its category.",
            "why_like": f"The {short_title[:60]} earns high marks for its solid construction, intuitive design, and reliable performance. Customers consistently highlight how it outperforms competitors at a similar price point, making it a smart investment for quality-conscious buyers.",
            "best_for": [
                "Daily use at home or on the go",
                "A thoughtful gift for any occasion",
                "Upgrading from older or budget alternatives",
                "Quality-focused shoppers on a budget",
                "Anyone who values reliability and durability",
            ],
            "pros": [
                "Excellent build quality and premium materials",
                "Outstanding value for the price point",
                "Highly rated by thousands of verified buyers",
                "Easy to use right out of the box",
                "Backed by Amazon's reliable return policy",
            ],
            "cons": [
                "May not suit users needing advanced features",
                "Limited color or style options available",
            ],
            "specs": {"Brand": "N/A", "Model": "N/A", "Weight": "N/A", "Dimensions": "N/A", "ASIN": "N/A"},
            "verdict": f"The {short_title[:60]} is a solid choice that delivers on its promises. With strong reviews and competitive pricing, it's well worth considering.",
            "faq": [
                {"q": "Is this product durable?", "a": "Yes, it's built with quality materials and has strong reviews for long-term durability."},
                {"q": "Does it come with a warranty?", "a": "Most products include a manufacturer warranty. Check the Amazon listing for specific details."},
            ],
            "final": f"Ready to upgrade? Tap the button below to check today's price and see why thousands of buyers love the {short_title[:50]}.",
        }
    return result


def _truncate_title(title: str, max_len: int = 70) -> str:
    """Truncate title at word boundary with '...' suffix."""
    if len(title) <= max_len:
        return title
    truncated = title[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len * 0.6:
        truncated = truncated[:last_space]
    return truncated + "..."


def _transform_and_upload_image(img_url: str, product_title: str = "") -> str:
    """
    Download Amazon image, transform it to avoid copyright, upload, and return new URL.
    Falls back to original URL if transformation fails.
    """
    if not img_url or not img_url.startswith("http"):
        return img_url
    
    if not IMAGE_TRANSFORM_ENABLED:
        logger.debug("[publisher] Image transformation disabled, using original")
        return img_url
    
    try:
        import config
        preset = getattr(config, "IMAGE_TRANSFORM_PRESET", "auto")
        
        from image_transformer import _download_image
        original_bytes = _download_image(img_url)
        if not original_bytes:
            logger.warning("[publisher] Could not download image for transformation")
            return img_url
        
        if preset == "auto":
            import random
            preset = random.choice(["oil_painting", "watercolor", "soft_glow", "vintage"])
        
        transformed = transform_image(original_bytes, preset=preset, add_shadow=True, output_format="PNG")
        
        if not transformed:
            logger.warning("[publisher] Image transformation failed, using original")
            return img_url
        
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', product_title[:50]) + ".png" if product_title else "product.png"
        new_url = _upload_image_to_host(transformed, safe_name)
        
        if new_url:
            logger.info(f"[publisher] Image transformed ({preset}) and uploaded: {new_url[:60]}...")
            return new_url
        else:
            logger.warning("[publisher] Image upload failed, using original")
            return img_url
            
    except Exception as e:
        logger.error(f"[publisher] Image transform error: {e}")
        return img_url


# ═══════════════════════════════════════════════════════════════════════════════
# NESTDEAL PREMIUM TEMPLATE - CLEAN AMAZON DEAL PAGE
# ═══════════════════════════════════════════════════════════════════════════════

def _build_article(product: dict, description: str) -> tuple[str, str]:
    """Build SEO title + clean HTML article for Blogger (NestDeal Premium Template)."""
    
    # ── Extract product data ──
    title        = product.get("title", "Product") or "Product"
    price        = product.get("price", "") or ""
    orig_price   = product.get("original_price", "") or ""
    aff_link     = product.get("aff_link", "") or ""
    img_url      = product.get("img_url", "") or ""
    all_images   = product.get("all_images", []) or []
    customer_reviews = product.get("customer_reviews", []) or []
    rating       = float(product.get("rating", 0) or 0)
    review_count = int(product.get("review_count", 0) or 0)
    features     = product.get("features", []) or []
    asin         = product.get("asin", "") or ""
    year         = datetime.now().year  # unused, kept for compatibility
    
    # ── Use original images (NO transformation) ──
    # If no all_images, fallback to img_url
    if not all_images and img_url:
        all_images = [img_url]
    # Ensure all_images has at least the main image
    if not all_images:
        all_images = [img_url] if img_url else []
    
    # ── Build affiliate link ──
    if not aff_link:
        import scraper as _scraper_mod
        aff_link = _scraper_mod.build_affiliate_url(product.get("clean_url", "#"))
    
    # ── Truncate titles ──
    seo_title = _truncate_title(title, 70)
    short_title = _truncate_title(title, 80)
    
    # ── Generate AI content ──
    ai = _ai_generate_content(title, price, description)
    
    # ── Detect category ──
    labels = _map_labels(title)
    category = labels[0] if labels else "Deals"
    
    # ── Calculate discount ──
    discount_pct = ""
    if price and orig_price:
        try:
            cp = float(re.sub(r"[^\d.]", "", price))
            op = float(re.sub(r"[^\d.]", "", orig_price))
            if op > cp > 0:
                discount_pct = str(int(round((op - cp) / op * 100)))
        except Exception:
            pass
    
    # ── Build price display ──
    has_price = bool(price and price != "N/A" and price.strip())
    if has_price:
        try:
            cp = float(re.sub(r"[^\d.]", "", price))
            price_display = f'${cp:.2f}' if not price.startswith('$') else price
        except Exception:
            price_display = price
        
        orig_display = ""
        if orig_price and discount_pct:
            try:
                op = float(re.sub(r"[^\d.]", "", orig_price))
                orig_display = f'${op:.2f}' if not orig_price.startswith('$') else orig_price
            except Exception:
                orig_display = orig_price
        
        price_html = f'<span class="nd-price-current">{price_display}</span>'
        if orig_display:
            price_html += f' <span class="nd-price-orig">{orig_display}</span>'
        if discount_pct:
            price_html += f' <span class="nd-price-save">SAVE {discount_pct}%</span>'
    else:
        price_html = '<span style="font-size:1rem;color:#6b7280;font-weight:500;">Check current price on Amazon</span>'
    
    # ── Build rating display ──
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    stars = "&#9733;" * full + ("&#9734;" if half else "") + "&#9734;" * (5 - full - half)
    reviews_str = f"{review_count:,}" if review_count else ""
    
    rating_html = ""
    if rating > 0:
        rating_html = f'<span style="color:#f59e0b;font-size:0.9rem;">{stars}</span> <span style="font-weight:700;color:#172033;font-size:0.9rem;">{rating:.1f}</span>'
        if reviews_str:
            rating_html += f' <span style="color:#6b7280;font-size:0.9rem;font-weight:500;">({reviews_str} reviews)</span>'
    
    # ── Build key tags (top features) ──
    tags = []
    if features:
        for f in features[:3]:
            if f and str(f).strip() and len(str(f)) < 30:
                tags.append(str(f).strip())
    if not tags:
        tags = ["Quality Product", "Great Value", "Amazon Choice"]
    
    tags_html = ""
    for t in tags:
        tags_html += f'<span style="display:inline-flex;align-items:center;gap:4px;background:#F7F8FA;padding:6px 12px;border-radius:6px;font-size:0.85rem;color:#374151;font-weight:500;border:1px solid #E4E7EC;"><span style="color:#6b7280;">&#10003;</span> {t}</span> '
    
    # ── Build Quick Take ──
    quick_take = ai.get("intro", "") or ai.get("verdict", "")
    if not quick_take:
        quick_take = f"A quality product that delivers on its promises. Check the details below to see if it's right for you."
    
    best_for = ai.get("best_for", [])
    standout = features[0] if features else "Quality construction"
    keep_in_mind = ai.get("cons", ["Check specifications before buying"])[0] if ai.get("cons") else "Check specifications before buying"
    
    # ── Build Why It Stands Out (features grid) ──
    features_grid = ""
    if features:
        cols = ""
        for i, f in enumerate(features[:4]):
            if f and str(f).strip():
                short_f = str(f)[:60] + "..." if len(str(f)) > 60 else str(f)
                cols += f'''<div class="nd-feature-card"><div class="nd-feature-icon">{["&#128736;", "&#128230;", "&#127968;", "&#128737;"][i % 4]}</div><div class="nd-feature-title">Feature {i+1}</div><div class="nd-feature-desc">{short_f}</div></div>'''
        if cols:
            features_grid = f'''<div class="nd-section"><h2>Why It Stands Out</h2><div class="nd-features">{cols}</div></div>'''
    
    # ── Build Pros & Cons ──
    pros_list = ai.get("pros", [])
    cons_list = ai.get("cons", [])
    
    pros_items = ""
    for p in pros_list[:5]:
        if p and str(p).strip():
            pros_items += f'<li class="nd-pc-item"><span class="nd-pc-icon nd-pc-icon-pros">&#10003;</span><span class="nd-pc-text">{p}</span></li>'
    
    cons_items = ""
    for c in cons_list[:3]:
        if c and str(c).strip():
            cons_items += f'<li class="nd-pc-item"><span class="nd-pc-icon nd-pc-icon-cons">&#10007;</span><span class="nd-pc-text">{c}</span></li>'
    
    pros_cons_html = ""
    if pros_items or cons_items:
        cons_list_html = cons_items if cons_items else '<li class="nd-pc-item"><span class="nd-pc-icon nd-pc-icon-cons">&#8212;</span><span class="nd-pc-text" style="color:#6b7280;">No significant concerns noted.</span></li>'
        pros_cons_html = f'''<div class="nd-pc-grid"><div class="nd-pros"><div class="nd-pc-title nd-pc-title-pros"><span class="nd-pc-badge nd-pc-badge-pros">&#10003;</span> Pros</div><ul>{pros_items}</ul></div><div class="nd-cons"><div class="nd-pc-title nd-pc-title-cons"><span class="nd-pc-badge nd-pc-badge-cons">&#10007;</span> Cons</div><ul>{cons_list_html}</ul></div></div>'''
    
    # ── Build Is It Right for You ──
    best_for_items = ai.get("best_for", [])
    cons_list_2 = ai.get("cons", [])
    
    yes_items = ""
    for item in best_for_items[:4]:
        if item and str(item).strip():
            yes_items += f'<li class="nd-pc-item"><span class="nd-pc-icon nd-pc-icon-pros">&#10003;</span><span class="nd-pc-text">{item}</span></li>'
    
    no_items = ""
    for item in cons_list_2[:3]:
        if item and str(item).strip():
            no_items += f'<li class="nd-pc-item"><span class="nd-pc-icon nd-pc-icon-cons">&#10007;</span><span class="nd-pc-text">{item}</span></li>'
    
    is_right_html = ""
    if yes_items or no_items:
        is_right_html = f'''<div class="nd-section"><h2>Is This Product Right for You?</h2><div class="nd-pc-grid"><div><div style="font-weight:700;color:#059669;margin-bottom:10px;font-size:0.9rem;text-transform:uppercase;letter-spacing:0.5px;">Yes, if you...</div><ul>{yes_items if yes_items else '<li class="nd-pc-item"><span class="nd-pc-icon nd-pc-icon-pros">&#10003;</span><span class="nd-pc-text">Looking for a quality product</span></li>'}</ul></div><div><div style="font-weight:700;color:#dc2626;margin-bottom:10px;font-size:0.9rem;text-transform:uppercase;letter-spacing:0.5px;">Look elsewhere, if you...</div><ul>{no_items if no_items else '<li class="nd-pc-item"><span class="nd-pc-icon nd-pc-icon-cons">&#10007;</span><span class="nd-pc-text">Have specific requirements</span></li>'}</ul></div></div></div>'''
    
    # ── Build Specs Table ──
    specs = ai.get("specs", {})
    specs_html = ""
    if specs:
        rows = ""
        for k, v in specs.items():
            if v and v != "N/A" and str(v).strip():
                rows += f'<tr><th>{k}</th><td>{v}</td></tr>'
        if rows:
            specs_html = f'''<div class="nd-section"><h2>Product Specifications</h2><table class="nd-specs-table">{rows}</table><p style="font-size:0.8rem;color:#9ca3af;margin:12px 0 0;font-style:italic;font-weight:500;">*Specifications may vary. Check Amazon for the latest details.</p></div>'''
    
    # ── Build What You Should Know ──
    know_items = []
    if features:
        know_items.append(("Check product dimensions", "Make sure it fits your space and requirements."))
    if cons_list:
        know_items.append(("Check weight capacity", "Ensure it meets your needs."))
    know_items.append(("Product only", "Check exactly what is included before purchasing."))
    
    know_html = ""
    if know_items:
        items = ""
        for i, (title_k, desc_k) in enumerate(know_items[:3]):
            items += f'''<div class="nd-pc-item"><span style="background:#FF9900;color:#fff;width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:0.8rem;font-weight:700;flex-shrink:0;">{i+1}</span><div><div style="font-weight:600;color:#172033;margin-bottom:2px;font-size:0.95rem;">{title_k}</div><div style="color:#6b7280;font-size:0.9rem;font-weight:500;">{desc_k}</div></div></div>'''
        know_html = f'''<div class="nd-section"><h2>What You Should Know</h2>{items}</div>'''
    
    # ── Build FAQ ──
    faq_items = ai.get("faq", [])
    faq_html = ""
    if faq_items:
        faqs = ""
        for faq in faq_items[:4]:
            q = faq.get("q", "")
            a = faq.get("a", "")
            if q and a:
                faqs += f'''<div class="nd-faq-item"><div class="nd-faq-q" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='block'?'none':'block';this.querySelector('.nd-faq-plus').textContent=this.nextElementSibling.style.display==='block'?'−':'+';">{q}<span class="nd-faq-plus">+</span></div><div class="nd-faq-a" style="display:none;">{a}</div></div>'''
        if faqs:
            faq_html = f'''<div class="nd-section"><h2>Frequently Asked Questions</h2>{faqs}</div>'''
    
    # ── Final Verdict ──
    verdict_text = ai.get("verdict", "")
    final_text = ai.get("final", "")
    verdict_html = ""
    if verdict_text or final_text:
        verdict_html = f'''<div class="nd-section" style="background:#F7F8FA;"><h2>Our Verdict</h2>{f'<p style="color:#172033;line-height:1.8;margin:0 0 8px;font-weight:500;">{verdict_text}</p>' if verdict_text else ''}{f'<p style="color:#6b7280;line-height:1.8;margin:0;font-weight:500;">{final_text}</p>' if final_text else ''}</div>'''

    # ── Build Customer Reviews (4+ stars only) ──
    reviews_html = ""
    if customer_reviews:
        reviews_items = ""
        for rev in customer_reviews[:4]:
            stars = rev.get("stars", 5)
            rev_title = rev.get("title", "")
            rev_body = rev.get("body", "")
            rev_name = rev.get("name", "Customer")
            stars_display = "★" * int(stars) + "☆" * (5 - int(stars))
            reviews_items += f'''<div class="nd-review"><div><span class="nd-review-stars">{stars_display}</span><span class="nd-review-title">{rev_title}</span></div><p class="nd-review-body">{rev_body[:200]}</p><span class="nd-review-name">— {rev_name}</span></div>'''
        if reviews_items:
            reviews_html = f'''<div class="nd-section"><h2>What Customers Say</h2><div class="nd-reviews">{reviews_items}</div></div>'''

    # ═══════════════════════════════════════════════════════════════════════════════
    # MAIN HTML - NESTDEAL CLEAN TEMPLATE v4 (FIXED)
    # ═══════════════════════════════════════════════════════════════════════════════
    
    # Build product images HTML
    images_html = ""
    for img in all_images[:3]:
        images_html += f'<div class="nd-img-card"><img src="{img}" alt="{short_title}" loading="eager"></div>'
    
    html = f'''<style>
/* ═══ NESTDEAL ARTICLE v4 — OVERRIDES BLOGGER TEMPLATE ═══ */
.nd-article{{width:100%;padding:0;margin:0;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#172033;line-height:1.7;background:transparent;}}
.nd-article *{{box-sizing:border-box;}}
.nd-article h1,.nd-article h2,.nd-article h3{{font-family:'Inter',-apple-system,sans-serif;color:#172033;margin:0;padding:0;line-height:1.3;}}
.nd-article h1{{font-size:clamp(1.4rem,4vw,2rem);font-weight:800;margin:0 0 12px;}}
.nd-article h2{{font-size:1.1rem;font-weight:800;margin:0 0 16px;}}
.nd-article h3{{font-size:0.95rem;font-weight:700;margin:0 0 8px;}}
.nd-article p{{margin:0 0 14px;font-weight:500;color:#374151;font-size:0.95rem;line-height:1.7;}}
.nd-article strong,.nd-article b{{font-weight:700;color:#172033;}}
.nd-article a{{color:#FF9900;font-weight:700;text-decoration:underline;}}
.nd-article ul{{list-style:none;padding:0;margin:0;}}
.nd-article li{{margin-bottom:8px;font-size:0.95rem;color:#374151;font-weight:500;}}

/* Breadcrumbs */
.nd-breadcrumb{{padding:12px 0;margin-bottom:12px;font-size:0.8rem;color:#9ca3af;display:flex;align-items:center;gap:6px;flex-wrap:wrap;}}
.nd-breadcrumb span{{font-weight:500;}}

/* Hero Card */
.nd-hero{{background:#fff;border:1px solid #E4E7EC;border-radius:16px;padding:24px;margin:0 0 16px;}}

/* Product Images — 3 columns desktop, scroll mobile */
.nd-img-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0 0 24px;}}
.nd-img-card{{overflow:hidden;border-radius:12px;background:#f9fafb;aspect-ratio:1/1;display:flex;align-items:center;justify-content:center;}}
.nd-img-card img{{width:100%;height:100%;object-fit:contain;transition:transform .3s ease;cursor:zoom-in;display:block;}}
.nd-img-card img:hover{{transform:scale(1.08);}}
@media(max-width:767px){{
  .nd-img-grid{{display:flex!important;overflow-x:auto!important;scroll-snap-type:x mandatory!important;-webkit-overflow-scrolling:touch!important;scrollbar-width:none!important;gap:12px!important;padding:4px 0!important;grid-template-columns:none!important;}}
  .nd-img-grid::-webkit-scrollbar{{display:none!important;}}
  .nd-img-card{{flex:0 0 75vw!important;scroll-snap-align:start!important;width:75vw!important;aspect-ratio:1/1!important;}}
}}

/* Quick Take */
.nd-qt{{background:#F7F8FA;border:1px solid #E4E7EC;border-radius:12px;padding:20px;margin:0 0 16px;}}
.nd-qt-label{{font-size:0.75rem;font-weight:700;color:#FF9900;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;}}
.nd-qt p{{color:#374151;font-size:0.95rem;line-height:1.7;margin:0 0 16px;font-weight:500;}}
.nd-qt-info{{border-top:1px solid #E4E7EC;padding-top:12px;margin-bottom:16px;}}
.nd-qt-row{{font-size:0.85rem;color:#6b7280;margin-bottom:4px;font-weight:500;line-height:1.5;}}
.nd-qt-row strong{{color:#172033;font-weight:700;}}

/* Price */
.nd-price{{margin-bottom:12px;}}
.nd-price-current{{font-size:1.4rem;font-weight:800;color:#172033;}}
.nd-price-orig{{font-size:0.95rem;color:#9ca3af;text-decoration:line-through;margin-left:8px;}}
.nd-price-save{{background:#dc2626;color:#fff;padding:3px 10px;border-radius:4px;font-size:0.8rem;font-weight:700;margin-left:8px;}}

/* CTA Button */
.nd-cta{{display:block;text-align:center;background:#FF9900;color:#fff!important;text-decoration:none!important;padding:14px 24px;border-radius:10px;font-weight:700;font-size:1rem;transition:background .2s ease;border:none;cursor:pointer;width:100%;}}
.nd-cta:hover{{background:#E68A00;}}
.nd-disclaimer{{font-size:0.7rem;color:#9ca3af;text-align:center;margin:8px 0 0;font-weight:500;}}

/* Section Cards */
.nd-section{{background:#fff;border:1px solid #E4E7EC;border-radius:16px;padding:24px;margin:0 0 16px;}}
.nd-section h2{{font-size:1.1rem;font-weight:800;color:#172033;margin:0 0 16px;}}

/* Features Grid */
.nd-features{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;}}
.nd-feature-card{{background:#F7F8FA;border-radius:12px;padding:16px;text-align:center;}}
.nd-feature-icon{{font-size:1.5rem;margin-bottom:8px;}}
.nd-feature-title{{font-weight:700;color:#172033;margin-bottom:4px;font-size:0.95rem;}}
.nd-feature-desc{{color:#6b7280;font-size:0.85rem;font-weight:500;line-height:1.4;}}
@media(max-width:767px){{.nd-features{{grid-template-columns:1fr 1fr!important;}}}}
@media(max-width:480px){{.nd-features{{grid-template-columns:1fr!important;}}}}

/* Pros & Cons */
.nd-pc-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;}}
.nd-pros{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;padding:20px;}}
.nd-cons{{background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:20px;}}
.nd-pc-title{{font-size:0.95rem;font-weight:700;margin:0 0 12px;display:flex;align-items:center;gap:6px;}}
.nd-pc-title-pros{{color:#166534;}}
.nd-pc-title-cons{{color:#991b1b;}}
.nd-pc-badge{{width:20px;height:20px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:0.75rem;color:#fff;}}
.nd-pc-badge-pros{{background:#059669;}}
.nd-pc-badge-cons{{background:#dc2626;}}
.nd-pc-item{{display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;font-size:0.95rem;}}
.nd-pc-icon{{flex-shrink:0;font-size:0.9rem;}}
.nd-pc-icon-pros{{color:#059669;}}
.nd-pc-icon-cons{{color:#dc2626;}}
.nd-pc-text{{color:#374151;font-weight:500;}}
@media(max-width:767px){{.nd-pc-grid{{grid-template-columns:1fr!important;}}}}

/* Specs Table */
.nd-specs-table{{width:100%;border-collapse:collapse;border-radius:12px;overflow:hidden;border:1px solid #E4E7EC;}}
.nd-specs-table td,.nd-specs-table th{{border:1px solid #E4E7EC;padding:12px 16px;font-size:0.9rem;font-weight:500;}}
.nd-specs-table th{{background:#F7F8FA;font-weight:700;color:#172033;text-align:left;width:40%;}}
.nd-specs-table td{{color:#374151;}}

/* FAQ */
.nd-faq-item{{margin-bottom:8px;border:1px solid #E4E7EC;border-radius:10px;overflow:hidden;}}
.nd-faq-q{{padding:14px 16px;cursor:pointer;font-weight:600;color:#172033;font-size:0.95rem;list-style:none;display:flex;justify-content:space-between;align-items:center;background:#fff;}}
.nd-faq-q:hover{{background:#F7F8FA;}}
.nd-faq-plus{{color:#9ca3af;font-size:1.2rem;font-weight:400;}}
.nd-faq-a{{padding:0 16px 14px;color:#6b7280;font-size:0.95rem;line-height:1.7;font-weight:500;}}

/* Reviews Grid */
.nd-reviews{{display:grid;grid-template-columns:1fr 1fr;gap:12px;}}
.nd-review{{background:#fff;border:1px solid #E4E7EC;border-radius:10px;padding:16px;}}
.nd-review-stars{{color:#FF9900;font-size:0.9rem;letter-spacing:1px;}}
.nd-review-title{{font-weight:700;color:#172033;font-size:0.9rem;margin-left:8px;}}
.nd-review-body{{color:#374151;font-size:0.85rem;line-height:1.6;margin:6px 0;font-weight:500;}}
.nd-review-name{{color:#9ca3af;font-size:0.8rem;font-weight:500;}}
@media(max-width:767px){{.nd-reviews{{grid-template-columns:1fr!important;}}}}

/* Final CTA */
.nd-final-cta{{background:#172033;border-radius:16px;padding:32px 24px;margin:0 0 16px;text-align:center;}}
.nd-final-cta h2{{font-size:1.2rem;font-weight:800;color:#fff;margin:0 0 8px;}}
.nd-final-cta p{{color:#d1d5db;margin:0 0 16px;font-size:0.95rem;font-weight:500;}}
.nd-final-cta .nd-cta{{display:inline-block;width:auto;padding:14px 40px;}}

/* Disclosure */
.nd-footer{{padding:16px 0;margin:0;border-top:1px solid #E4E7EC;}}
.nd-footer p{{font-size:0.75rem;color:#9ca3af;margin:0;line-height:1.6;font-weight:500;}}
</style>

<article class="nd-article">

<!-- BREADCRUMBS -->
<div class="nd-breadcrumb">
  <span style="color:#6b7280;">Home</span>
  <span>&#8250;</span>
  <span style="color:#6b7280;">{category}</span>
  <span>&#8250;</span>
  <span style="color:#172033;font-weight:600;">{short_title[:35]}</span>
</div>

<!-- HERO SECTION -->
<div class="nd-hero">
  
  <h1>{short_title}</h1>
  
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px;">
    {rating_html}
  </div>
  
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px;">
    {tags_html}
  </div>
  
  <!-- Product Images -->
  <div class="nd-img-grid">{images_html}</div>
  
  <!-- Quick Take -->
  <div class="nd-qt">
    <p>{quick_take[:200]}</p>
    <div class="nd-qt-info">
      <div class="nd-qt-row"><strong>Best for:</strong> {best_for[0] if best_for else "Quality-conscious buyers"}</div>
      <div class="nd-qt-row"><strong>Standout:</strong> {standout[:50]}</div>
      <div class="nd-qt-row"><strong>Keep in mind:</strong> {keep_in_mind[:60]}</div>
    </div>
    <div class="nd-price">{price_html}</div>
    <a class="nd-cta" href="{aff_link}" target="_blank" rel="nofollow sponsored noopener">Check Price on Amazon &#8594;</a>
    <p class="nd-disclaimer">As an Amazon Associate, I earn from qualifying purchases.</p>
  </div>
  
</div>

<!-- WHY IT STANDS OUT -->
{features_grid}

<!-- PROS & CONS -->
{pros_cons_html}

<!-- IS IT RIGHT FOR YOU -->
{is_right_html}

<!-- PRODUCT SPECIFICATIONS -->
{specs_html}

<!-- WHAT YOU SHOULD KNOW -->
{know_html}

<!-- FAQ -->
{faq_html}

<!-- FINAL VERDICT -->
{verdict_html}

<!-- CUSTOMER REVIEWS -->
{reviews_html}

<!-- FINAL CTA -->
<div class="nd-final-cta">
  <h2>Ready to Buy?</h2>
  <p>Check the latest price and availability on Amazon.</p>
  <a class="nd-cta" href="{aff_link}" target="_blank" rel="nofollow sponsored noopener">Check Price on Amazon &#8594;</a>
  <p style="font-size:0.75rem;color:#9ca3af;margin:12px 0 0;font-weight:500;">Prices and availability may change.</p>
</div>

<!-- AFFILIATE DISCLOSURE -->
<div class="nd-footer">
  <p><strong>Disclosure:</strong> As an Amazon Associate, I earn from qualifying purchases. Product prices and availability are accurate as of the date/time indicated and are subject to change.</p>
</div>

</article>'''

    return seo_title, html


# ── PRE-PUBLISH VALIDATION ─────────────────────────────────────────────────────

def _validate_article(html: str, title: str = "") -> list[str]:
    """Validate article before publishing. Returns list of issues found."""
    issues = []

    if not html:
        issues.append("Empty HTML content")
        return issues

    html_lower = html.lower()

    # Check for MAD currency
    if "mad" in html_lower and "$" not in html:
        issues.append("MAD currency detected instead of USD")

    # Check for placeholder patterns
    placeholders = ["add your", "edit brand", "point here", "placeholder", "lorem ipsum", "edit this"]
    for ph in placeholders:
        if ph in html_lower:
            issues.append(f"Placeholder text found: '{ph}'")

    # Check for broken affiliate links
    if 'href="#"' in html or 'href="#"' in html:
        issues.append("Broken link: href='#'")

    # Check for wrong affiliate tag
    if "yourtag-20" in html or "mytag-20" in html:
        issues.append("Wrong affiliate tag (yourtag-20 or mytag-20)")

    # Check for dazzledeals00-20 tag
    if "dazzledeals00-20" not in html:
        issues.append("Missing correct affiliate tag (dazzledeals00-20)")

    # Check for empty title
    if not title or len(title.strip()) < 5:
        issues.append("Title too short or empty")

    return issues


# ── RATE LIMIT TRACKING ────────────────────────────────────────────────────────
import time as _time

_last_api_call = 0
_MIN_DELAY_BETWEEN_CALLS = 3  # seconds between any two API calls

def _rate_limit_wait():
    """Ensure minimum delay between API calls."""
    global _last_api_call
    elapsed = _time.time() - _last_api_call
    if elapsed < _MIN_DELAY_BETWEEN_CALLS:
        _time.sleep(_MIN_DELAY_BETWEEN_CALLS - elapsed)
    _last_api_call = _time.time()


def _api_request_with_retry(method, url, max_retries=3, **kwargs):
    """Make an API request with exponential backoff on 429 errors."""
    _rate_limit_wait()
    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)
            if resp.status_code == 429:
                wait = min(60 * (2 ** attempt), 300)  # 60s, 120s, 240s, max 300s
                logger.warning(f"[blogger_api] 429 rate limit — waiting {wait}s (attempt {attempt+1}/{max_retries+1})")
                _time.sleep(wait)
                _last_api_call = _time.time()
                continue
            return resp
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                _time.sleep(5 * (attempt + 1))
                continue
            raise
    # If all retries exhausted, make final attempt and return whatever we get
    return requests.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)


# ── PUBLISH POST ───────────────────────────────────────────────────────────────

def publish_post(
    product:      dict,
    description:  str,
    labels:       list | None = None,
    html_content: str = "",
    title:        str = "",
    publish_now:  bool = True,
    **kwargs,
) -> dict:
    """
    Publish a post to Blogger via API v3.
    Returns: {status, post_id, post_url, error}
    """
    if not is_configured():
        msg = "❌ Blogger API not configured."
        return {"status": "failed", "post_id": None, "post_url": None, "error": msg}

    try:
        if not html_content:
            title, html_content = _build_article(product, description)
        if not title:
            prod_title = product.get("title", "Product")[:70]
            price      = product.get("price", "")
            title = f"{prod_title} — Best Deal at {price}" if price else f"{prod_title} Review"

        labels = _map_labels(title, labels)

        html_content = (html_content or "").replace("\x00", "")

        access_token = _get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "title":   title.strip(),
            "content": html_content,
            "labels":  labels or [],
        }

        params = {}
        if not publish_now:
            params["isDraft"] = "true"

        resp = _api_request_with_retry(
            "POST",
            f"{BLOGGER_BASE}/blogs/{BLOG_ID}/posts/",
            headers=headers,
            json=payload,
            params=params,
            timeout=30,
        )

        if resp.status_code in (200, 201):
            data     = resp.json()
            post_id  = data.get("id", "")
            post_url = data.get("url", "")
            logger.info(f"[blogger_api] ✅ Published: {title[:50]} → {post_url}")
            
            # 🔥 CRITICAL TRIGGER: Trigger Telegram channel broadcast automatically if live
            if publish_now:
                prod_price = product.get("price", "N/A")
                _send_telegram_alert(title, prod_price, post_url)

            return {
                "status":   "success",
                "post_id":  post_id,
                "post_url": post_url,
                "error":    None,
            }

        error_msg = f"HTTP {resp.status_code}: {resp.text[:300]}"
        logger.error(f"[blogger_api] ❌ {error_msg}")
        return {"status": "failed", "post_id": None, "post_url": None, "error": error_msg}

    except Exception as e:
        logger.error(f"[blogger_api] publish_post exception: {e}")
        return {"status": "failed", "post_id": None, "post_url": None, "error": str(e)}


# ── LIST POSTS ─────────────────────────────────────────────────────────────────

def list_recent_posts(max_results: int = 10) -> list[dict]:
    if not is_configured():
        return []
    try:
        token = _get_access_token()
        resp  = _api_request_with_retry(
            "GET",
            f"{BLOGGER_BASE}/blogs/{BLOG_ID}/posts",
            headers={"Authorization": f"Bearer {token}"},
            params={"maxResults": max_results, "status": "live"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("items", [])
    except Exception as e:
        logger.error(f"[blogger_api] list_recent_posts: {e}")
    return []


def delete_post(post_id: str) -> bool:
    if not is_configured():
        return False
    try:
        token = _get_access_token()
        resp  = _api_request_with_retry(
            "DELETE",
            f"{BLOGGER_BASE}/blogs/{BLOG_ID}/posts/{post_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        return resp.status_code == 204
    except Exception as e:
        logger.error(f"[blogger_api] delete_post: {e}")
        return False
