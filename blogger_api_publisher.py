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
import html as _html
from urllib.parse import quote_plus as _quote_plus
from datetime import datetime

logger = logging.getLogger(__name__)

# Set to True during bulk fix to skip Telegram alerts
SKIP_TELEGRAM_ALERT = False


def _safe_text(s) -> str:
    """Decode entities repeatedly (AI double-encodes), then escape safely."""
    t = str(s or "")
    for _ in range(3):
        u = _html.unescape(t)
        if u == t:
            break
        t = u
    return _html.escape(t, quote=True)


_LEAD_SYM = re.compile(r'^[✓✗✔✘×✖\-–—•*+\s]+')


def _li_text(s) -> str:
    """List-item text: decoded, leading symbols stripped (markup adds icons)."""
    return _LEAD_SYM.sub('', _safe_text(s)).strip()

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
        f' "scores": {{"value": 8.5, "build": 9.0, "features": 8.0, "ease": 8.5}},\n'
        f' "alternatives": [{{"name": "real competing product name", "reason": "1 sentence why a buyer might prefer it"}} x2],\n'
        f' "verdict": "1 strong concluding sentence",\n'
        f' "faq": [{{"q": "question", "a": "answer"}} for 2 common questions],\n'
        f' "final": "1 sentence Call to Action"}}\n'
        f"Rules: Be objective, authoritative, and concise. No markdown formatting in the strings. "
        f"Alternative names must be real products shoppers can find on Amazon. "
        f"Use real Unicode characters (not HTML entities) in visible text."
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
            "scores": {},
            "alternatives": [],
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

    # Sanitized display strings (AI may emit entities; decode + escape)
    safe_title   = _safe_text(short_title)
    safe_intro   = _safe_text(ai.get("intro", ""))
    safe_why     = _safe_text(ai.get("why_like", ""))
    safe_verdict = _safe_text(ai.get("verdict", ""))
    safe_final   = _safe_text(ai.get("final", ""))
    
    # Calculate Editor Score (out of 10) based on Amazon rating
    editor_score = round((rating / 5.0) * 10, 1) if rating > 0 else 9.2
    score_color = "#059669" if editor_score >= 8.5 else "#d97706"

    # ── Criteria scores (Value / Build / Features / Ease) + Final score ──
    _CRITERIA = [("value", "Value for Money"), ("build", "Build Quality"),
                 ("features", "Features"), ("ease", "Ease of Use")]
    _raw_scores = ai.get("scores", {}) or {}
    _base = editor_score if editor_score else 7.5
    _gen_offsets = {"value": 0.0, "build": 0.3, "features": -0.2, "ease": 0.1}
    _scores = {}
    for _k, _label in _CRITERIA:
        try:
            _v = float(_raw_scores.get(_k, ""))
            if not (1 <= _v <= 10):
                raise ValueError
        except (ValueError, TypeError):
            _v = _base + _gen_offsets[_k]
        _scores[_k] = round(max(1.0, min(10.0, _v)), 1)
    _final_score = round(sum(_scores.values()) / len(_scores), 1)
    _recommend = "buy" if _final_score >= 8.0 else ("consider" if _final_score >= 6.0 else "skip")
    _recommend_text = {"buy": "We Recommend", "consider": "Worth Considering", "skip": "We Say Skip It"}[_recommend]
    _recommend_cls = _recommend
    _bars_html = ""
    for _k, _label in _CRITERIA:
        _v = _scores[_k]
        _cls = "high" if _v >= 7.5 else ("mid" if _v >= 5 else "low")
        _bars_html += (
            f'<div class="rvw-score-row"><span class="rvw-score-label">{_label}</span>'
            f'<div class="rvw-score-track"><div class="rvw-score-fill {_cls}" style="width:{min(100, _v * 10):.0f}%"></div></div>'
            f'<span class="rvw-score-val">{_v:.1f}</span></div>'
        )
    # Hidden data span: live theme JS renders sidebar verdict card + score bars
    _nd_span = (
        f'<span data-nd-review data-score="{_final_score:.1f}" data-recommend="{_recommend}" '
        f'data-verdict="{_safe_text(ai.get("verdict", ""))}" data-amazon-url="{aff_link}" '
        f'data-score-value="{_scores["value"]:.1f}" data-score-build="{_scores["build"]:.1f}" '
        f'data-score-features="{_scores["features"]:.1f}" style="display:none"></span>'
    )
    
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
    svg_check   = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>'
    svg_x       = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>'
    svg_info    = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>'
    svg_star    = '<svg viewBox="0 0 24 24"><path d="M12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>'
    svg_cart    = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="21" r="1"></circle><circle cx="20" cy="21" r="1"></circle><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path></svg>'
    svg_shield  = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>'
    svg_clock   = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>'

    # ── Build Pros & Cons ──
    pros_list = ai.get("pros", [])
    cons_list = ai.get("cons", [])

    pros_html = "".join([f'<li class="rvw-pc-item"><span class="rvw-pc-icon pros">{svg_check}</span><span>{_li_text(p)}</span></li>' for p in pros_list if p])
    cons_html = "".join([f'<li class="rvw-pc-item"><span class="rvw-pc-icon cons">{svg_x}</span><span>{_li_text(c)}</span></li>' for c in cons_list if c])

    if not cons_html:
        cons_html = f'<li class="rvw-pc-item"><span class="rvw-pc-icon cons">{svg_info}</span><span>No major drawbacks identified for this price range.</span></li>'

    # ── Build Specs Table ──
    specs = ai.get("specs", {})
    specs_rows = ""
    for k, v in specs.items():
        if v and str(v).strip() and v != "N/A":
            specs_rows += f'<tr><td class="rvw-spec-label">{_safe_text(k)}</td><td class="rvw-spec-value">{_safe_text(v)}</td></tr>'

    # ── Build FAQ (accordion) ──
    faq_items = ai.get("faq", [])
    faq_html = ""
    for i, faq in enumerate(faq_items):
        q, a = faq.get("q", ""), faq.get("a", "")
        if q and a:
            faq_html += f'<details class="rvw-faq-card"{" open" if i == 0 else ""}><summary class="rvw-faq-q">{_safe_text(q)}</summary><p class="rvw-faq-a">{_safe_text(a)}</p></details>'

    # ── Build "Who is this for" ──
    best_for = ai.get("best_for", [])
    best_for_html = "".join([f'<li><span class="rvw-mini-icon">{svg_check}</span>{_li_text(item)}</li>' for item in best_for if item])

    # ── Build Alternatives (other Amazon products) + Comparison ──
    _AFF_TAG = os.environ.get("AFFILIATE_TAG", "dazzledeals00-20")
    _raw_alts = [a for a in (ai.get("alternatives", []) or [])
                 if isinstance(a, dict) and str(a.get("name", "")).strip()]
    _alts = [a for a in _raw_alts
             if not str(a.get("name", "")).strip().lower().startswith(("alternative to", "comparable", "other option"))][:2]
    _alt_items = []
    for _a in _alts:
        _an = _safe_text(str(_a.get("name", ""))[:60])
        _ar = _safe_text(str(_a.get("reason", "") or _a.get("why", "") or "Worth comparing before you decide.")[:160])
        _au = f"https://www.amazon.com/s?k={_quote_plus(str(_a.get('name', ''))[:60])}&tag={_AFF_TAG}"
        _alt_items.append((_an, _ar, _au))
    _alt_cards = ""
    _cmp_cols = ""
    _cmp_cells = ""
    for (_an, _ar, _au) in _alt_items:
        _alt_cards += (
            f'<div class="rvw-alt-card"><div class="rvw-alt-label">Alternative</div>'
            f'<h4>{_an}</h4><p>{_ar}</p>'
            f'<a class="rvw-btn rvw-btn-alt" href="{_au}" target="_blank" rel="nofollow sponsored noopener">{svg_cart} See Price on Amazon</a></div>'
        )
        _cmp_cols += f"<th>{_an}</th>"
        _cmp_cells += f'<td>{_ar}<br><a href="{_au}" target="_blank" rel="nofollow sponsored noopener">Check price →</a></td>'
    _alts_html = (
        f'<h2 id="rvw-alts">Top Alternatives to Consider</h2><p>Not fully convinced? These competing picks are worth a look before you buy.</p><div class="rvw-alt-grid">{_alt_cards}</div>'
        if _alt_cards else ""
    )
    _compare_html = ""
    if _cmp_cols:
        _cmp_best = _safe_text(str(best_for[0] if best_for else ai.get("verdict", "This product"))[:120])
        _cmp_rating = f"{rating}/5" if rating > 0 else f"{round(editor_score/2,1)}/5"
        _compare_html = (
            f'<h2 id="rvw-compare">Head-to-Head Comparison</h2><div class="rvw-compare-wrap"><table class="rvw-compare-table">'
            f'<thead><tr><th>Feature</th><th>{safe_title} <span class="rvw-badge-winner">Reviewed</span></th>{_cmp_cols}</tr></thead>'
            f'<tbody><tr><td>Best for</td><td class="rvw-winner">{_cmp_best}</td>{_cmp_cells}</tr>'
            f'<tr><td>Rating</td><td class="rvw-winner">{_cmp_rating}</td>{"<td>Check listing</td>" * len(_alt_items)}</tr>'
            f'</tbody></table></div>'
        )

    # ── TOC extra links (only for sections that exist) ──
    _toc_extra = ""
    if specs_rows:
        _toc_extra += '<li><a href="#rvw-specs">Specifications</a></li>'
    if _alt_cards:
        _toc_extra += '<li><a href="#rvw-alts">Alternatives</a></li>'
    if _cmp_cols:
        _toc_extra += '<li><a href="#rvw-compare">Comparison</a></li>'
    if faq_html:
        _toc_extra += '<li><a href="#rvw-faq">FAQ</a></li>'

    # ── Images Setup ──
    main_img = all_images[0] if all_images else ""
    thumbs_html = ""
    if len(all_images) > 1:
        for img in all_images[1:5]:
            thumbs_html += f'<img src="{img}" class="rvw-thumb" alt="Gallery image" loading="lazy">'

    # ── JSON-LD Review schema (SEO rich results) ──
    import json as _json
    _ld = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": short_title,
        "description": (ai.get("intro", "") or "")[:300],
        "review": {
            "@type": "Review",
            "reviewRating": {"@type": "Rating", "ratingValue": _final_score, "bestRating": 10},
            "author": {"@type": "Organization", "name": "NestDeal"},
        },
    }
    if main_img:
        _ld["image"] = main_img
    if rating > 0 and review_count >= 5:
        _ld["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": round(rating, 1), "reviewCount": review_count}
    _ld_html = '<script type="application/ld+json">' + _json.dumps(_ld, ensure_ascii=False).replace("<", "\\u003c") + '</script>'

    # ── Star rating widget (visual, out of 5) ──
    def _stars(value: float) -> str:
        out = ""
        for i in range(1, 6):
            fill = "full" if value >= i else ("half" if value >= i - 0.5 else "empty")
            out += f'<span class="rvw-star {fill}">{svg_star}</span>'
        return out

    star_rating_html = _stars(rating if rating > 0 else (editor_score / 2))

    # ── Discount badge ──
    discount_html = ""
    if has_price and orig_price:
        try:
            op = float(re.sub(r"[^\d.]", "", orig_price))
            cp2 = float(re.sub(r"[^\d.]", "", price))
            if op > cp2 > 0:
                pct = round((1 - cp2 / op) * 100)
                if pct > 0:
                    discount_html = f'<span class="rvw-discount-badge">-{pct}%</span>'
        except Exception:
            discount_html = ""

    orig_price_html = f'<span class="rvw-price-original">{orig_price}</span>' if (orig_price and discount_html) else ""

    # ── Current Date for Trust Bar ──
    current_month_year = datetime.now().strftime("%B %Y")

    # ── Sidebar (sticky buy box + mini scores + more picks + key specs) ──
    _side_specs = "".join(
        f'<li><span>{_safe_text(str(k))}</span><strong>{_safe_text(str(v))[:42]}</strong></li>'
        for k, v in list(specs.items())[:5]
        if v and str(v).strip() and v != "N/A"
    )
    _side_picks = "".join(
        f'<div class="rvw-side-pick"><a href="{_au}" target="_blank" rel="nofollow sponsored noopener">{_an}</a>'
        f'<a class="rvw-side-check" href="{_au}" target="_blank" rel="nofollow sponsored noopener">Check price →</a></div>'
        for (_an, _ar, _au) in _alt_items
    )
    _side_img = f'<img src="{main_img}" alt="{safe_title}" loading="lazy">' if main_img else ""
    _side_picks_card = ""
    if _side_picks:
        _side_picks_card = '<div class="rvw-side-card"><h4>More Top Picks</h4>' + _side_picks + "</div>"
    _side_specs_card = ""
    if _side_specs:
        _side_specs_card = '<div class="rvw-side-card"><h4>Key Specs</h4><ul class="rvw-side-specs">' + _side_specs + "</ul></div>"
    _side_html = (
        f'<aside class="rvw-sidecol">'
        f'<div class="rvw-side-card rvw-side-buy">'
        f'<h4>Today&apos;s Deal</h4>'
        f'{_side_img}'
        f'<div class="t">{safe_title[:70]}</div>'
        f'<div class="rvw-stars">{star_rating_html}</div>'
        f'<div class="p">{price_display}</div>'
        f'<a href="{aff_link}" class="rvw-btn" target="_blank" rel="nofollow sponsored noopener">{svg_cart} Check Price on Amazon</a>'
        f'<div class="rvw-btn-subtext">Updated {current_month_year}</div>'
        f'</div>'
        f'<div class="rvw-side-card"><h4>Our Score</h4>'
        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">'
        f'<div class="rvw-score-circle sm"><span class="val">{_final_score:.1f}</span><span class="max">/10</span></div>'
        f'<div class="rvw-recommend {_recommend_cls}" style="margin:0;">{_recommend_text}</div>'
        f'</div>{_bars_html}</div>'
        f'{_side_picks_card}'
        f'{_side_specs_card}'
        f'<div class="rvw-side-card"><h4>Why Trust Us</h4>'
        f'<p class="rvw-side-note">Independent testing &amp; research. As an Amazon Associate we earn from qualifying purchases — at no extra cost to you.</p></div>'
        f'</aside>'
    )

    # ═══════════════════════════════════════════════════════════════════════════════
    # HTML & CSS ASSEMBLY — "EDITORIAL PICK" TEMPLATE (Amazon-affiliate review standard)
    # ═══════════════════════════════════════════════════════════════════════════════

    html = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@600;700;800&family=Inter:wght@400;500;600;700&display=swap');

.rvw-wrapper {{
    --rvw-ink: #17181c;
    --rvw-body: #40434c;
    --rvw-muted: #767a86;
    --rvw-border: #e6e7eb;
    --rvw-surface: #f7f7f9;
    --rvw-card: #ffffff;
    --rvw-accent: #e8590c;
    --rvw-accent-dark: #c94a09;
    --rvw-accent-soft: #fff1e8;
    --rvw-good: #1a8a5e;
    --rvw-good-soft: #eafaf3;
    --rvw-bad: #d6334c;
    --rvw-bad-soft: #fdeef0;
    --rvw-gold: #f5a623;
    --rvw-navy: #171b2e;
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    color: var(--rvw-body);
    line-height: 1.7;
    font-size: 17px;
    width: 100%;
    max-width: 1160px;
    margin: 0 auto;
}}
.rvw-wrapper * {{ box-sizing: border-box; }}
.rvw-wrapper h2 {{ font-family: 'Poppins', sans-serif; font-size: 1.6rem; font-weight: 800; color: var(--rvw-ink); margin: 2.75rem 0 1.1rem; letter-spacing: -0.01em; }}
.rvw-wrapper h3 {{ font-family: 'Poppins', sans-serif; font-size: 1.2rem; font-weight: 700; color: var(--rvw-ink); margin: 1.5rem 0 0.75rem; }}
.rvw-wrapper p {{ margin: 0 0 1.25rem; }}
.rvw-wrapper a {{ color: var(--rvw-accent-dark); text-decoration: underline; font-weight: 600; text-underline-offset: 2px; }}
.rvw-wrapper a:hover {{ color: var(--rvw-accent); }}

/* Disclosure */
.rvw-disclosure {{ font-size: 0.8rem; color: var(--rvw-muted); background: var(--rvw-surface); border: 1px solid var(--rvw-border); border-radius: 8px; padding: 10px 14px; margin-bottom: 20px; }}
.rvw-disclosure a {{ color: var(--rvw-muted); text-decoration: underline; font-weight: 500; }}

/* Trust Bar */
.rvw-trust-bar {{ display: flex; flex-wrap: wrap; gap: 18px; align-items: center; padding: 0 0 20px; margin-bottom: 12px; border-bottom: 1px solid var(--rvw-border); font-size: 0.85rem; color: var(--rvw-muted); font-weight: 600; }}
.rvw-trust-item {{ display: flex; align-items: center; gap: 6px; }}
.rvw-trust-item svg {{ color: var(--rvw-accent); flex-shrink: 0; }}

/* Hero */
.rvw-hero {{ background: var(--rvw-card); border: 1px solid var(--rvw-border); border-radius: 16px; padding: 28px; margin: 24px 0 40px; box-shadow: 0 1px 3px rgba(23,24,28,0.04), 0 12px 28px -12px rgba(23,24,28,0.10); }}
.rvw-hero-grid {{ display: grid; grid-template-columns: 0.9fr 1.1fr; gap: 36px; align-items: start; }}
@media (max-width: 760px) {{ .rvw-hero-grid {{ grid-template-columns: 1fr; gap: 20px; }} .rvw-hero {{ padding: 18px; border-radius: 14px; }} }}

.rvw-badge-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }}
.rvw-badge {{ display: inline-flex; align-items: center; gap: 5px; background: var(--rvw-navy); color: #fff; font-family: 'Poppins', sans-serif; font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; padding: 6px 12px; border-radius: 999px; }}
.rvw-badge.alt {{ background: var(--rvw-accent-soft); color: var(--rvw-accent-dark); }}

.rvw-hero-image-container {{ display: flex; flex-direction: column; gap: 10px; }}
.rvw-main-img {{ width: 100%; aspect-ratio: 1/1; object-fit: contain; border-radius: 12px; background: var(--rvw-surface); border: 1px solid var(--rvw-border); padding: 18px; }}
.rvw-thumbs-row {{ display: flex; gap: 8px; overflow-x: auto; scrollbar-width: none; }}
.rvw-thumbs-row::-webkit-scrollbar {{ display: none; }}
.rvw-thumb {{ width: 68px; height: 68px; flex-shrink: 0; object-fit: contain; border-radius: 8px; border: 1px solid var(--rvw-border); background: #fff; padding: 4px; }}

.rvw-hero-content h1, .rvw-hero-content h2 {{ font-size: 1.45rem; line-height: 1.35; margin: 0 0 10px; }}

.rvw-rating-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; flex-wrap: wrap; }}
.rvw-stars {{ display: inline-flex; gap: 2px; }}
.rvw-star svg {{ width: 18px; height: 18px; }}
.rvw-star.full svg {{ fill: var(--rvw-gold); stroke: var(--rvw-gold); }}
.rvw-star.half svg {{ fill: var(--rvw-gold); stroke: var(--rvw-gold); opacity: 0.5; }}
.rvw-star.empty svg {{ fill: none; stroke: var(--rvw-border); stroke-width: 1.5; }}
.rvw-rating-num {{ font-weight: 800; color: var(--rvw-ink); font-family: 'Poppins', sans-serif; font-size: 0.95rem; }}
.rvw-review-count {{ font-size: 0.85rem; color: var(--rvw-muted); font-weight: 500; }}

.rvw-editor-score {{ display: inline-flex; align-items: center; gap: 10px; background: var(--rvw-surface); border: 1px solid var(--rvw-border); border-radius: 10px; padding: 8px 14px; margin: 10px 0 18px; }}
.rvw-editor-score-num {{ font-family: 'Poppins', sans-serif; font-weight: 800; font-size: 1.3rem; color: var(--rvw-accent-dark); }}
.rvw-editor-score-label {{ font-size: 0.75rem; color: var(--rvw-muted); font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; line-height: 1.3; }}

.rvw-hero-intro {{ font-size: 1.02rem; color: var(--rvw-body); margin-bottom: 20px; }}

.rvw-price-row {{ display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 18px; }}
.rvw-price-current {{ font-family: 'Poppins', sans-serif; font-size: 1.9rem; font-weight: 800; color: var(--rvw-ink); }}
.rvw-price-original {{ font-size: 1.05rem; color: var(--rvw-muted); text-decoration: line-through; font-weight: 500; }}
.rvw-discount-badge {{ background: var(--rvw-bad-soft); color: var(--rvw-bad); font-weight: 800; font-size: 0.8rem; padding: 4px 10px; border-radius: 6px; }}

.rvw-btn-container {{ display: flex; flex-direction: column; gap: 9px; }}
.rvw-btn {{ display: flex; align-items: center; justify-content: center; gap: 9px; background: var(--rvw-accent); color: #fff !important; text-decoration: none !important; padding: 16px 24px; border-radius: 10px; font-family: 'Poppins', sans-serif; font-weight: 700; font-size: 1.05rem; transition: all 0.15s ease; box-shadow: 0 6px 16px -4px rgba(232,89,12,0.45); width: 100%; text-align: center; border: none; }}
.rvw-btn:hover {{ background: var(--rvw-accent-dark); transform: translateY(-1px); }}
.rvw-btn-subtext {{ text-align: center; font-size: 0.78rem; color: var(--rvw-muted); font-weight: 500; }}

/* TL;DR box */
.rvw-tldr {{ background: var(--rvw-navy); color: #e9eaf2; border-radius: 14px; padding: 24px 28px; margin: 32px 0; }}
.rvw-tldr-label {{ font-family: 'Poppins', sans-serif; font-size: 0.75rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.08em; color: var(--rvw-gold); margin-bottom: 8px; }}
.rvw-tldr p {{ color: #e9eaf2; margin: 0; font-size: 1.02rem; }}

/* Pros & Cons */
.rvw-pc-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 28px 0; }}
@media (max-width: 760px) {{ .rvw-pc-grid {{ grid-template-columns: 1fr; }} }}
.rvw-pc-box {{ border-radius: 14px; padding: 22px 24px; border: 1px solid var(--rvw-border); }}
.rvw-pc-box.pros {{ background: var(--rvw-good-soft); border-color: #c8ecdb; }}
.rvw-pc-box.cons {{ background: var(--rvw-bad-soft); border-color: #f5cdd4; }}
.rvw-pc-title {{ font-family: 'Poppins', sans-serif; font-size: 1.02rem; font-weight: 800; margin: 0 0 14px; display: flex; align-items: center; gap: 8px; }}
.rvw-pc-box.pros .rvw-pc-title {{ color: var(--rvw-good); }}
.rvw-pc-box.cons .rvw-pc-title {{ color: var(--rvw-bad); }}
.rvw-pc-list {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 11px; }}
.rvw-pc-item {{ display: flex; align-items: flex-start; gap: 10px; font-size: 0.94rem; color: var(--rvw-ink); font-weight: 500; line-height: 1.5; }}
.rvw-pc-icon {{ flex-shrink: 0; margin-top: 2px; display: flex; }}
.rvw-pc-icon.pros {{ color: var(--rvw-good); }}
.rvw-pc-icon.cons {{ color: var(--rvw-bad); }}

/* Analysis */
.rvw-analysis-box {{ background: var(--rvw-surface); border: 1px solid var(--rvw-border); padding: 24px 26px; margin: 28px 0; border-radius: 14px; }}
.rvw-analysis-box p:last-child {{ margin-bottom: 0; }}

/* Who is this for */
.rvw-target-audience {{ background: var(--rvw-card); border: 1px solid var(--rvw-border); border-radius: 14px; padding: 24px 26px; margin: 28px 0; }}
.rvw-target-audience ul {{ list-style: none; padding: 0; margin: 14px 0 0; display: flex; flex-direction: column; gap: 11px; }}
.rvw-target-audience li {{ display: flex; align-items: center; gap: 10px; font-weight: 600; color: var(--rvw-ink); font-size: 0.95rem; }}
.rvw-mini-icon {{ color: var(--rvw-good); display: flex; flex-shrink: 0; }}

/* Specs Table */
.rvw-specs-table {{ width: 100%; border-collapse: collapse; margin: 22px 0; border: 1px solid var(--rvw-border); border-radius: 12px; overflow: hidden; }}
.rvw-specs-table tr:nth-child(even) {{ background: var(--rvw-surface); }}
.rvw-specs-table td {{ padding: 13px 16px; border-bottom: 1px solid var(--rvw-border); font-size: 0.92rem; }}
.rvw-specs-table tr:last-child td {{ border-bottom: none; }}
.rvw-spec-label {{ font-weight: 700; color: var(--rvw-ink); width: 40%; }}
.rvw-spec-value {{ color: var(--rvw-body); font-weight: 500; }}

/* FAQ Accordion */
.rvw-faq-container {{ display: flex; flex-direction: column; gap: 10px; margin-top: 20px; }}
.rvw-faq-card {{ background: var(--rvw-card); border: 1px solid var(--rvw-border); border-radius: 10px; padding: 4px 20px; }}
.rvw-faq-q {{ cursor: pointer; padding: 14px 0; font-family: 'Poppins', sans-serif; font-weight: 700; font-size: 0.98rem; color: var(--rvw-ink); list-style: none; }}
.rvw-faq-q::-webkit-details-marker {{ display: none; }}
.rvw-faq-q::after {{ content: '+'; float: right; font-size: 1.3rem; color: var(--rvw-accent); font-weight: 400; }}
.rvw-faq-card[open] .rvw-faq-q::after {{ content: '−'; }}
.rvw-faq-a {{ margin: 0 0 16px; color: var(--rvw-body); font-size: 0.94rem; }}

/* Final Verdict */
.rvw-verdict-box {{ background: linear-gradient(135deg, var(--rvw-navy) 0%, #23283f 100%); color: #fff; border-radius: 16px; padding: 40px 32px; text-align: center; margin: 48px 0 24px; }}
.rvw-verdict-box h2 {{ color: #fff; margin-top: 0; font-size: 1.7rem; }}
.rvw-verdict-box p {{ color: #c7c9d6; font-size: 1.02rem; max-width: 600px; margin: 0 auto 28px; }}
.rvw-verdict-box .rvw-btn {{ max-width: 380px; margin: 0 auto; background: var(--rvw-accent); }}
.rvw-verdict-box .rvw-btn:hover {{ background: var(--rvw-accent-dark); }}

/* Alternatives */
.rvw-alt-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 20px 0 28px; }}
@media (max-width: 760px) {{ .rvw-alt-grid {{ grid-template-columns: 1fr; }} }}
.rvw-alt-card {{ background: var(--rvw-card); border: 1px solid var(--rvw-border); border-radius: 14px; padding: 22px 24px; }}
.rvw-alt-label {{ display: inline-block; font-family: 'Poppins', sans-serif; font-size: 0.7rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.06em; color: var(--rvw-accent-dark); background: var(--rvw-accent-soft); padding: 4px 12px; border-radius: 999px; margin-bottom: 10px; }}
.rvw-alt-card h4 {{ font-family: 'Poppins', sans-serif; font-size: 1.05rem; font-weight: 700; color: var(--rvw-ink); margin: 0 0 8px; }}
.rvw-alt-card p {{ font-size: 0.92rem; margin-bottom: 16px; }}
.rvw-btn-alt {{ font-size: 0.95rem; padding: 13px 20px; }}

/* Comparison table */
.rvw-compare-wrap {{ overflow-x: auto; margin: 20px 0 28px; border: 1px solid var(--rvw-border); border-radius: 12px; }}
.rvw-compare-table {{ width: 100%; border-collapse: collapse; min-width: 560px; }}
.rvw-compare-table th, .rvw-compare-table td {{ padding: 13px 16px; font-size: 0.9rem; text-align: left; border-bottom: 1px solid var(--rvw-border); vertical-align: top; }}
.rvw-compare-table thead th {{ background: var(--rvw-navy); color: #fff; font-family: 'Poppins', sans-serif; font-size: 0.85rem; }}
.rvw-compare-table tr:last-child td {{ border-bottom: none; }}
.rvw-compare-table td:first-child {{ font-weight: 700; color: var(--rvw-ink); white-space: nowrap; }}
.rvw-winner {{ background: var(--rvw-good-soft); font-weight: 600; }}
.rvw-badge-winner {{ display: inline-block; font-size: 0.65rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; background: var(--rvw-good); color: #fff; padding: 3px 9px; border-radius: 999px; margin-left: 6px; vertical-align: middle; }}

/* Score box (criteria ratings + final) */
.rvw-scorebox {{ background: var(--rvw-card); border: 1px solid var(--rvw-border); border-radius: 16px; overflow: hidden; margin: 28px 0; }}
.rvw-scorebox-head {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; background: var(--rvw-navy); color: #fff; padding: 16px 22px; }}
.rvw-scorebox-head h2 {{ color: #fff; margin: 0; font-size: 1.1rem; }}
.rvw-score-circle {{ width: 64px; height: 64px; border-radius: 50%; border: 4px solid var(--rvw-gold); display: flex; flex-direction: column; align-items: center; justify-content: center; background: rgba(245,166,35,.08); flex-shrink: 0; }}
.rvw-score-circle .val {{ font-family: 'Poppins', sans-serif; font-size: 1.25rem; font-weight: 800; color: #fff; line-height: 1; }}
.rvw-score-circle .max {{ font-size: 0.65rem; color: rgba(255,255,255,.6); }}
.rvw-scorebox-body {{ padding: 20px 22px; }}
.rvw-scorebox-body p:last-child {{ margin-bottom: 0; }}
.rvw-recommend {{ display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; border-radius: 8px; font-size: 0.85rem; font-weight: 700; margin-bottom: 14px; }}
.rvw-recommend.buy {{ background: var(--rvw-good-soft); color: var(--rvw-good); border: 1px solid #c8ecdb; }}
.rvw-recommend.consider {{ background: var(--rvw-accent-soft); color: var(--rvw-accent-dark); border: 1px solid #FDE68A; }}
.rvw-recommend.skip {{ background: var(--rvw-bad-soft); color: var(--rvw-bad); border: 1px solid #f5cdd4; }}
.rvw-score-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }}
.rvw-score-row:last-child {{ margin-bottom: 0; }}
.rvw-score-label {{ font-size: 0.8rem; color: var(--rvw-muted); font-weight: 600; width: 120px; flex-shrink: 0; }}
@media (max-width: 480px) {{ .rvw-score-label {{ width: 96px; }} }}
.rvw-score-track {{ flex: 1; height: 8px; background: var(--rvw-surface); border: 1px solid var(--rvw-border); border-radius: 5px; overflow: hidden; }}
.rvw-score-fill {{ height: 100%; border-radius: 5px; background: var(--rvw-accent); }}
.rvw-score-fill.low {{ background: #EF4444; }}
.rvw-score-fill.mid {{ background: var(--rvw-accent); }}
.rvw-score-fill.high {{ background: var(--rvw-good); }}
.rvw-score-val {{ font-size: 0.8rem; font-weight: 800; color: var(--rvw-ink); width: 30px; text-align: right; }}

/* Table of contents */
.rvw-toc {{ background: var(--rvw-surface); border: 1px solid var(--rvw-border); border-radius: 12px; padding: 18px 22px; margin: 20px 0 8px; }}
.rvw-toc-title {{ font-family: 'Poppins', sans-serif; font-size: 0.85rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.06em; color: var(--rvw-ink); margin-bottom: 10px; }}
.rvw-toc ul {{ list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 8px 18px; }}
.rvw-toc li {{ margin: 0; }}
.rvw-toc a {{ font-size: 0.88rem; font-weight: 600; }}

/* Two-column layout with filled sticky sidebar */
.rvw-cols {{ display: grid; grid-template-columns: minmax(0,1fr) 320px; gap: 28px; align-items: start; margin-top: 24px; }}
@media (max-width: 900px) {{ .rvw-cols {{ grid-template-columns: 1fr; }} }}
.rvw-maincol {{ min-width: 0; }}
.rvw-maincol > h2:first-child {{ margin-top: 0; }}
.rvw-sidecol {{ position: sticky; top: 76px; display: flex; flex-direction: column; gap: 16px; min-width: 0; }}
@media (max-width: 900px) {{ .rvw-sidecol {{ position: static; }} }}
.rvw-side-card {{ background: var(--rvw-card); border: 1px solid var(--rvw-border); border-radius: 14px; padding: 18px; }}
.rvw-side-card h4 {{ font-family: 'Poppins', sans-serif; font-size: 0.8rem; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 12px; color: var(--rvw-ink); }}
.rvw-side-buy img {{ width: 100%; aspect-ratio: 1/1; object-fit: contain; background: var(--rvw-surface); border: 1px solid var(--rvw-border); border-radius: 10px; padding: 12px; margin-bottom: 12px; }}
.rvw-side-buy .t {{ font-weight: 700; font-size: 0.92rem; color: var(--rvw-ink); margin-bottom: 8px; line-height: 1.4; }}
.rvw-side-buy .rvw-stars {{ margin-bottom: 4px; }}
.rvw-side-buy .p {{ font-family: 'Poppins', sans-serif; font-size: 1.4rem; font-weight: 800; color: var(--rvw-ink); margin: 6px 0 12px; }}
.rvw-side-buy .rvw-btn {{ font-size: 0.92rem; padding: 13px 16px; }}
.rvw-score-circle.sm {{ width: 52px; height: 52px; border-width: 3px; }}
.rvw-score-circle.sm .val {{ font-size: 1rem; }}
.rvw-side-card .rvw-score-label {{ width: 100px; font-size: 0.72rem; }}
.rvw-side-pick {{ padding: 10px 0; }}
.rvw-side-pick + .rvw-side-pick {{ border-top: 1px solid var(--rvw-border); }}
.rvw-side-pick a:first-child {{ display: block; font-weight: 700; font-size: 0.85rem; color: var(--rvw-ink); text-decoration: none; margin-bottom: 2px; }}
.rvw-side-check {{ font-size: 0.78rem; font-weight: 700; }}
.rvw-side-specs {{ list-style: none; padding: 0; margin: 0; }}
.rvw-side-specs li {{ display: flex; justify-content: space-between; gap: 10px; font-size: 0.78rem; padding: 7px 0; }}
.rvw-side-specs li + li {{ border-top: 1px solid var(--rvw-border); }}
.rvw-side-specs span {{ color: var(--rvw-muted); }}
.rvw-side-specs strong {{ color: var(--rvw-ink); text-align: right; font-weight: 600; }}
.rvw-side-note {{ font-size: 0.75rem; color: var(--rvw-muted); line-height: 1.6; margin: 0; }}
</style>

<div class="rvw-wrapper">
    {_nd_span}{_ld_html}

    <div class="rvw-disclosure">{svg_shield} As an Amazon Associate, we earn from qualifying purchases. Our editorial team tests and researches products independently of any commercial relationship.</div>

    <div class="rvw-trust-bar">
        <div class="rvw-trust-item">{svg_shield} <span>Expert Review</span></div>
        <div class="rvw-trust-item">{svg_clock} <span>Updated {current_month_year}</span></div>
        <div class="rvw-trust-item">{svg_cart} <span>{review_count:,}+ verified ratings</span></div>
    </div>

    <!-- TOC -->
    <div class="rvw-toc">
        <div class="rvw-toc-title">In This Review</div>
        <ul>
            <li><a href="#rvw-scores">Our Scores</a></li>
            <li><a href="#rvw-pros">Pros &amp; Cons</a></li>
            {_toc_extra}
            <li><a href="#rvw-verdict">Final Verdict</a></li>
        </ul>
    </div>

    <!-- HERO -->
    <div class="rvw-hero">
        <div class="rvw-hero-grid">

            <div class="rvw-hero-image-container">
                <img src="{main_img}" class="rvw-main-img" alt="{safe_title}">
                {f'<div class="rvw-thumbs-row">{thumbs_html}</div>' if thumbs_html else ''}
            </div>

            <div class="rvw-hero-content">
                <div class="rvw-badge-row">
                    <span class="rvw-badge">{svg_check} Editor's Pick</span>
                    {f'<span class="rvw-badge alt">Best Value</span>' if editor_score >= 8.5 else ''}
                </div>

                <h2>{safe_title}</h2>

                <div class="rvw-rating-row">
                    <span class="rvw-stars">{star_rating_html}</span>
                    <span class="rvw-rating-num">{rating if rating > 0 else round(editor_score/2,1)}/5</span>
                    <span class="rvw-review-count">({review_count:,} ratings)</span>
                </div>

                <div class="rvw-editor-score">
                    <span class="rvw-editor-score-num">{editor_score}</span>
                    <span class="rvw-editor-score-label">Editor's<br>Score /10</span>
                </div>

                <p class="rvw-hero-intro">{safe_intro}</p>

                <div class="rvw-price-row">
                    <span class="rvw-price-current">{price_display}</span>
                    {orig_price_html}
                    {discount_html}
                </div>

                <div class="rvw-btn-container">
                    <a href="{aff_link}" class="rvw-btn" target="_blank" rel="nofollow sponsored noopener">
                        {svg_cart} Check Price on Amazon
                    </a>
                    <div class="rvw-btn-subtext">Price accurate as of {current_month_year}. Terms apply.</div>
                </div>
            </div>
        </div>
    </div>

    <!-- MAIN + SIDEBAR -->
    <div class="rvw-cols">
    <div class="rvw-maincol">

    <!-- SCORES -->
    <div class="rvw-scorebox" id="rvw-scores">
        <div class="rvw-scorebox-head">
            <h2>Our Scores</h2>
            <div class="rvw-score-circle"><span class="val">{_final_score:.1f}</span><span class="max">/10</span></div>
        </div>
        <div class="rvw-scorebox-body">
            <div class="rvw-recommend {_recommend_cls}">{(svg_check if _recommend == "buy" else (svg_x if _recommend == "skip" else svg_info))} {_recommend_text}</div>
            <p>{safe_verdict or "A strong, well-rounded choice that delivers real value for the price."}</p>
            {_bars_html}
        </div>
    </div>

    <!-- PROS AND CONS -->
    <h2 id="rvw-pros">The Good and The Bad</h2>
    <div class="rvw-pc-grid">
        <div class="rvw-pc-box pros">
            <h3 class="rvw-pc-title">{svg_check} What We Like</h3>
            <ul class="rvw-pc-list">{pros_html}</ul>
        </div>
        <div class="rvw-pc-box cons">
            <h3 class="rvw-pc-title">{svg_x} What Could Be Better</h3>
            <ul class="rvw-pc-list">{cons_html}</ul>
        </div>
    </div>

    <!-- IN-DEPTH ANALYSIS -->
    <h2>In-Depth Analysis</h2>
    <div class="rvw-analysis-box">
        <p>{safe_why or "This product stands out due to its exceptional balance of performance and price. During our evaluation, it consistently met or exceeded expectations for its category."}</p>
    </div>

    <!-- WHO IS THIS FOR -->
    <div class="rvw-target-audience">
        <h3 style="margin-top:0;">Who Should Buy This?</h3>
        <ul>{best_for_html}</ul>
    </div>

    <!-- SPECIFICATIONS -->
    {f'<h2 id="rvw-specs">Key Specifications</h2><table class="rvw-specs-table"><tbody>{specs_rows}</tbody></table>' if specs_rows else ''}

    <!-- FAQ -->
    {f'<h2 id="rvw-faq">Frequently Asked Questions</h2><div class="rvw-faq-container">{faq_html}</div>' if faq_html else ''}

    <!-- ALTERNATIVES -->
    {_alts_html}

    <!-- COMPARISON -->
    {_compare_html}

    </div>
    {_side_html}
    </div>

    <!-- FINAL VERDICT -->
    <div class="rvw-verdict-box" id="rvw-verdict">
        <h2>Final Verdict</h2>
        <p>{safe_verdict or "A highly recommended product that delivers excellent value for your money."} {safe_final or "Check the link below for the latest deals."}</p>
        <a href="{aff_link}" class="rvw-btn" target="_blank" rel="nofollow sponsored noopener">
            {svg_cart} Check Current Price on Amazon
        </a>
    </div>

</div>
"""

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
