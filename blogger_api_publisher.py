# START OF FILE blogger_api_publisher.py

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
        f"You are an expert product reviewer for a world-class tech and lifestyle site (like Wirecutter or RTINGS).\n"
        f"Write a highly professional, honest, and direct review for:\n"
        f"Title: {short_title}\nPrice: {price}\n\n"
        f"Return ONLY valid JSON with this exact structure:\n"
        f'{{"intro": "2-3 sentences summarizing the bottom line (why it is good or bad)",\n'
        f' "why_like": "1 detailed paragraph analyzing its performance, build quality, and value",\n'
        f' "best_for": ["3 specific types of users who should buy this"],\n'
        f' "pros": ["3-4 strong pros"],\n'
        f' "cons": ["2-3 honest cons"],\n'
        f' "specs": {{"Brand": "...", "Key Feature": "...", "Weight/Size": "..."}},\n'
        f' "verdict": "1 strong concluding sentence",\n'
        f' "faq": [{{"q": "question", "a": "answer"}} for 2 common questions],\n'
        f' "final": "1 sentence Call to Action"}}\n'
        f"Rules: Be objective, authoritative, and concise. No markdown formatting in the strings."
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
                text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
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
            "intro": f"After evaluating the {short_title}, we found it to be a highly competent performer in its category. It balances premium features with an accessible price point, making it a strong contender for most buyers.",
            "why_like": f"During our analysis of the {short_title[:60]}, we were particularly impressed by its solid construction and intuitive design. Unlike cheaper alternatives that cut corners, this model delivers consistent reliability. The feature set is robust enough for demanding users, yet accessible enough for beginners, representing excellent value for money.",
            "best_for": [
                "Users looking for the best price-to-performance ratio",
                "Buyers upgrading from entry-level models",
                "Those who prioritize long-term reliability over gimmicks"
            ],
            "pros": [
                "Exceptional build quality and durability",
                "Highly competitive price point",
                "Intuitive and easy to use out of the box",
                "Backed by overwhelmingly positive user reviews"
            ],
            "cons": [
                "Lacks some ultra-premium niche features",
                "Design is functional rather than flashy"
            ],
            "specs": {"Category": "Consumer Goods", "Value Rating": "Excellent", "Ease of Use": "High"},
            "verdict": f"The {short_title[:60]} easily earns our recommendation as a top-tier choice that won't break the bank.",
            "faq": [
                {"q": "Is this worth the investment?", "a": "Yes, given its durability and performance metrics, it offers excellent long-term value."},
                {"q": "How does it compare to budget options?", "a": "It significantly outperforms budget alternatives in both lifespan and daily usability."}
            ],
            "final": "Check the current availability and secure the best price using the link below.",
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


# ═══════════════════════════════════════════════════════════════════════════════
# WORLD-CLASS REVIEW TEMPLATE (WIRECUTTER / RTINGS STYLE)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_article(product: dict, description: str) -> tuple[str, str]:
    """Build SEO title + world-class HTML article for Blogger."""
    
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
    
    # Ensure images
    if not all_images and img_url:
        all_images = [img_url]
    if not all_images:
        all_images = [img_url] if img_url else []
    
    # Affiliate link
    if not aff_link:
        import scraper as _scraper_mod
        aff_link = _scraper_mod.build_affiliate_url(product.get("clean_url", "#"))
    
    # Truncate titles
    seo_title = _truncate_title(title, 70)
    short_title = _truncate_title(title, 80)
    
    # Generate AI content
    ai = _ai_generate_content(title, price, description)
    
    # Calculate Editor Score (out of 10) based on Amazon rating
    editor_score = round((rating / 5.0) * 10, 1) if rating > 0 else 9.2
    score_color = "#059669" if editor_score >= 8.5 else "#d97706"
    
    # Price formatting
    has_price = bool(price and price != "N/A" and price.strip())
    if has_price:
        try:
            cp = float(re.sub(r"[^\d.]", "", price))
            price_display = f'${cp:.2f}' if not price.startswith('$') else price
        except Exception:
            price_display = price
    else:
        price_display = "Check on Amazon"

    # ── SVG Icons ──
    svg_check = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#059669" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>'
    svg_x = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>'
    svg_info = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>'
    svg_star = '<svg width="16" height="16" viewBox="0 0 24 24" fill="#FF9900" stroke="#FF9900" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>'

    # ── Build Pros & Cons ──
    pros_list = ai.get("pros", [])
    cons_list = ai.get("cons", [])
    
    pros_html = "".join([f'<li class="wr-pc-item"><span class="wr-pc-icon">{svg_check}</span><span>{p}</span></li>' for p in pros_list if p])
    cons_html = "".join([f'<li class="wr-pc-item"><span class="wr-pc-icon">{svg_x}</span><span>{c}</span></li>' for c in cons_list if c])
    
    if not cons_html:
        cons_html = f'<li class="wr-pc-item"><span class="wr-pc-icon">{svg_info}</span><span>No major drawbacks identified for this price range.</span></li>'

    # ── Build Specs Table ──
    specs = ai.get("specs", {})
    specs_rows = ""
    for k, v in specs.items():
        if v and str(v).strip() and v != "N/A":
            specs_rows += f'<tr><td class="wr-spec-label">{k}</td><td class="wr-spec-value">{v}</td></tr>'

    # ── Build FAQ ──
    faq_items = ai.get("faq", [])
    faq_html = ""
    for faq in faq_items:
        q, a = faq.get("q", ""), faq.get("a", "")
        if q and a:
            faq_html += f'<div class="wr-faq-card"><h4 class="wr-faq-q">{q}</h4><p class="wr-faq-a">{a}</p></div>'

    # ── Build "Who is this for" ──
    best_for = ai.get("best_for", [])
    best_for_html = "".join([f'<li>{svg_check} {item}</li>' for item in best_for if item])

    # ── Images Setup ──
    main_img = all_images[0] if all_images else ""
    thumbs_html = ""
    if len(all_images) > 1:
        for img in all_images[1:4]:
            thumbs_html += f'<img src="{img}" class="wr-thumb" alt="Gallery image" loading="lazy">'

    # ── Current Date for Trust Bar ──
    current_month_year = datetime.now().strftime("%B %Y")

    # ═══════════════════════════════════════════════════════════════════════════════
    # HTML & CSS ASSEMBLY
    # ═══════════════════════════════════════════════════════════════════════════════
    
    html = f'''
<style>
/* ═══ WORLD-CLASS REVIEW TEMPLATE (WIRECUTTER/RTINGS STYLE) ═══ */
.wr-review-wrapper {{
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    color: #0f172a;
    line-height: 1.7;
    max-width: 100%;
    margin: 0 auto;
    font-size: 17px;
}}
.wr-review-wrapper * {{ box-sizing: border-box; }}
.wr-review-wrapper h2 {{ font-size: 1.75rem; font-weight: 800; color: #0f172a; margin: 2.5rem 0 1rem; letter-spacing: -0.02em; border-bottom: 2px solid #f1f5f9; padding-bottom: 0.5rem; }}
.wr-review-wrapper h3 {{ font-size: 1.3rem; font-weight: 700; color: #1e293b; margin: 1.5rem 0 0.75rem; }}
.wr-review-wrapper p {{ margin: 0 0 1.25rem; color: #334155; }}
.wr-review-wrapper a {{ color: #2563eb; text-decoration: underline; font-weight: 600; text-underline-offset: 2px; }}
.wr-review-wrapper a:hover {{ color: #1d4ed8; }}

/* Trust Bar */
.wr-trust-bar {{ display: flex; flex-wrap: wrap; gap: 16px; align-items: center; padding: 12px 16px; background: #f8fafc; border-radius: 8px; margin-bottom: 32px; font-size: 0.85rem; color: #475569; font-weight: 500; border: 1px solid #e2e8f0; }}
.wr-trust-item {{ display: flex; align-items: center; gap: 6px; }}
.wr-trust-item svg {{ color: #059669; }}

/* The Bottom Line Box (Hero) */
.wr-hero-box {{ border: 2px solid #e2e8f0; border-top: 4px solid #FF9900; border-radius: 12px; background: #fff; padding: 32px; margin-bottom: 40px; box-shadow: 0 10px 15px -3px rgb(0 0 0 / 0.05); position: relative; }}
.wr-hero-badge {{ position: absolute; top: -14px; left: 32px; background: #FF9900; color: #fff; font-weight: 800; padding: 4px 16px; border-radius: 20px; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
.wr-hero-grid {{ display: grid; grid-template-columns: 1fr 1.2fr; gap: 32px; align-items: start; }}
@media (max-width: 768px) {{ .wr-hero-grid {{ grid-template-columns: 1fr; gap: 24px; }} .wr-hero-box {{ padding: 20px; }} .wr-hero-badge {{ left: 20px; }} }}

/* Hero Images */
.wr-hero-image-container {{ display: flex; flex-direction: column; gap: 12px; }}
.wr-main-img {{ width: 100%; aspect-ratio: 4/3; object-fit: contain; border-radius: 8px; background: #f8fafc; border: 1px solid #f1f5f9; padding: 16px; }}
.wr-thumbs-row {{ display: flex; gap: 8px; overflow-x: auto; scrollbar-width: none; }}
.wr-thumbs-row::-webkit-scrollbar {{ display: none; }}
.wr-thumb {{ width: 80px; height: 80px; object-fit: contain; border-radius: 6px; border: 1px solid #e2e8f0; background: #fff; padding: 4px; }}

/* Hero Content */
.wr-hero-content h2 {{ border: none; margin: 0 0 16px; padding: 0; font-size: 1.5rem; line-height: 1.3; }}
.wr-score-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }}
.wr-score-circle {{ background: {score_color}; color: #fff; font-weight: 800; font-size: 1.2rem; width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; border-radius: 8px; }}
.wr-score-text {{ font-size: 0.9rem; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }}
.wr-hero-intro {{ font-size: 1.05rem; font-weight: 500; color: #334155; line-height: 1.6; margin-bottom: 24px; }}

/* CTA Button */
.wr-btn-container {{ display: flex; flex-direction: column; gap: 8px; margin-top: 24px; }}
.wr-btn {{ display: flex; align-items: center; justify-content: center; gap: 8px; background: #FF9900; color: #fff !important; text-decoration: none !important; padding: 16px 24px; border-radius: 8px; font-weight: 800; font-size: 1.1rem; transition: all 0.2s; box-shadow: 0 4px 6px -1px rgba(255, 153, 0, 0.2); width: 100%; text-align: center; border: none; }}
.wr-btn:hover {{ background: #E68A00; transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(255, 153, 0, 0.3); }}
.wr-btn-subtext {{ text-align: center; font-size: 0.8rem; color: #64748b; font-weight: 500; }}

/* Pros & Cons Side-by-Side */
.wr-pc-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin: 32px 0; }}
@media (max-width: 768px) {{ .wr-pc-grid {{ grid-template-columns: 1fr; gap: 16px; }} }}
.wr-pc-box {{ border-radius: 12px; padding: 24px; }}
.wr-pc-box.pros {{ background: #f0fdf4; border: 1px solid #bbf7d0; }}
.wr-pc-box.cons {{ background: #fef2f2; border: 1px solid #fecaca; }}
.wr-pc-title {{ font-size: 1.1rem; font-weight: 800; margin: 0 0 16px; display: flex; align-items: center; gap: 8px; }}
.wr-pc-box.pros .wr-pc-title {{ color: #166534; }}
.wr-pc-box.cons .wr-pc-title {{ color: #991b1b; }}
.wr-pc-list {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 12px; }}
.wr-pc-item {{ display: flex; align-items: flex-start; gap: 12px; font-size: 0.95rem; color: #334155; font-weight: 500; line-height: 1.5; }}
.wr-pc-icon {{ flex-shrink: 0; margin-top: 2px; }}

/* Specs Table */
.wr-specs-table {{ width: 100%; border-collapse: collapse; margin: 24px 0; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }}
.wr-specs-table tr:nth-child(even) {{ background: #f8fafc; }}
.wr-specs-table td {{ padding: 14px 16px; border-bottom: 1px solid #e2e8f0; font-size: 0.95rem; }}
.wr-spec-label {{ font-weight: 700; color: #0f172a; width: 40%; }}
.wr-spec-value {{ color: #475569; font-weight: 500; }}

/* Deep Dive Section */
.wr-analysis-box {{ background: #fff; border-left: 4px solid #3b82f6; padding: 24px 32px; margin: 32px 0; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05); border-radius: 0 8px 8px 0; }}
.wr-analysis-box p:last-child {{ margin-bottom: 0; }}

/* Who is this for */
.wr-target-audience {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px; margin: 32px 0; }}
.wr-target-audience ul {{ list-style: none; padding: 0; margin: 16px 0 0; display: flex; flex-direction: column; gap: 12px; }}
.wr-target-audience li {{ display: flex; align-items: center; gap: 12px; font-weight: 600; color: #334155; }}

/* FAQ */
.wr-faq-container {{ display: flex; flex-direction: column; gap: 16px; margin-top: 24px; }}
.wr-faq-card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; }}
.wr-faq-q {{ margin: 0 0 8px; font-size: 1.05rem; color: #0f172a; }}
.wr-faq-a {{ margin: 0; color: #475569; font-size: 0.95rem; }}

/* Final Verdict Box */
.wr-verdict-box {{ background: #0f172a; color: #fff; border-radius: 12px; padding: 40px 32px; text-align: center; margin: 48px 0 24px; }}
.wr-verdict-box h2 {{ color: #fff; border: none; margin-top: 0; font-size: 2rem; }}
.wr-verdict-box p {{ color: #cbd5e1; font-size: 1.1rem; max-width: 600px; margin: 0 auto 32px; }}
.wr-verdict-box .wr-btn {{ max-width: 400px; margin: 0 auto; }}
</style>

<div class="wr-review-wrapper">

    <!-- TRUST SIGNALS -->
    <div class="wr-trust-bar">
        <div class="wr-trust-item">{svg_check} <span><strong>Expert Review</strong></span></div>
        <div class="wr-trust-item">{svg_info} <span>Updated: <strong>{current_month_year}</strong></span></div>
        <div class="wr-trust-item">{svg_star} <span>Based on <strong>{review_count:,}+</strong> user ratings</span></div>
    </div>

    <!-- THE BOTTOM LINE (HERO) -->
    <div class="wr-hero-box">
        <div class="wr-hero-badge">Our Top Pick</div>
        <div class="wr-hero-grid">
            
            <div class="wr-hero-image-container">
                <img src="{main_img}" class="wr-main-img" alt="{short_title}">
                {f'<div class="wr-thumbs-row">{thumbs_html}</div>' if thumbs_html else ''}
            </div>
            
            <div class="wr-hero-content">
                <h2>{short_title}</h2>
                
                <div class="wr-score-row">
                    <div class="wr-score-circle">{editor_score}</div>
                    <div class="wr-score-text">
                        <div style="color:#0f172a;">Editor's Score</div>
                        <div style="font-size:0.75rem; font-weight:500;">Out of 10</div>
                    </div>
                </div>
                
                <p class="wr-hero-intro">{ai.get("intro", "")}</p>
                
                <div class="wr-btn-container">
                    <a href="{aff_link}" class="wr-btn" target="_blank" rel="nofollow sponsored noopener">
                        View on Amazon — {price_display}
                    </a>
                    <div class="wr-btn-subtext">Prices are accurate at the time of publication.</div>
                </div>
            </div>
        </div>
    </div>

    <!-- PROS AND CONS -->
    <h2>The Good and The Bad</h2>
    <div class="wr-pc-grid">
        <div class="wr-pc-box pros">
            <h3 class="wr-pc-title">{svg_check} Reasons to Buy</h3>
            <ul class="wr-pc-list">{pros_html}</ul>
        </div>
        <div class="wr-pc-box cons">
            <h3 class="wr-pc-title">{svg_x} Reasons to Avoid</h3>
            <ul class="wr-pc-list">{cons_html}</ul>
        </div>
    </div>

    <!-- IN-DEPTH ANALYSIS -->
    <h2>In-Depth Analysis</h2>
    <div class="wr-analysis-box">
        <p>{ai.get("why_like", "This product stands out due to its exceptional balance of performance and price. During our evaluation, it consistently met or exceeded expectations for its category.")}</p>
    </div>

    <!-- WHO IS THIS FOR -->
    <div class="wr-target-audience">
        <h3 style="margin-top:0;">Who should buy this?</h3>
        <ul>{best_for_html}</ul>
    </div>

    <!-- SPECIFICATIONS -->
    {f'<h2>Key Specifications</h2><table class="wr-specs-table"><tbody>{specs_rows}</tbody></table>' if specs_rows else ''}

    <!-- FAQ -->
    {f'<h2>Frequently Asked Questions</h2><div class="wr-faq-container">{faq_html}</div>' if faq_html else ''}

    <!-- FINAL VERDICT -->
    <div class="wr-verdict-box">
        <h2>Final Verdict</h2>
        <p>{ai.get("verdict", "A highly recommended product that delivers excellent value for your money.")} {ai.get("final", "Check the link below for the latest deals.")}</p>
        <a href="{aff_link}" class="wr-btn" target="_blank" rel="nofollow sponsored noopener">
            Check Current Price on Amazon
        </a>
    </div>

</div>
'''

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

# END OF FILE blogger_api_publisher.py
