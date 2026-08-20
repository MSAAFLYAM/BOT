# main.py — Amazon Affiliate Bot v4 HYBRID
# Pipeline: Scrape → Channel → WordPress → Blogger → Canvas → Sheets → Pinterest
# 2026 Edition — AI-adaptive articles, Pinterest API, test mode, power dashboard

from dotenv import load_dotenv
load_dotenv()

import logging, time, os, re, html, threading, asyncio
from datetime import datetime, timedelta, timezone

import telebot
import config
import scraper
import content_generator
import image_processor
import wordpress_publisher
import blogger_api_publisher
import queue_manager
import scheduler

# ── sheets_handler: supprimé — guard pour compatibilité ─────────────────────
try:
    import sheets_handler
    _sheets_ok = True
except ImportError:
    class _SheetsStub:
        """Stub silencieux pour sheets_handler supprimé."""
        def __getattr__(self, name):
            def _noop(*a, **k):
                return None
            return _noop
    sheets_handler = _SheetsStub()
    _sheets_ok = False
    logging.getLogger(__name__).info(
        "[startup] sheets_handler absent — stub actif"
    )

# ── Optional modules — graceful fallback if not deployed ─────────────────────
try:
    import pinterest_api
    _pinterest_api_available = True
except ImportError:
    pinterest_api = None
    _pinterest_api_available = False
    logging.getLogger(__name__).warning("[startup] pinterest_api.py not found")

try:
    import sheets_repair
    _sheets_repair_available = True
except ImportError:
    sheets_repair = None
    _sheets_repair_available = False
    logging.getLogger(__name__).warning("[startup] sheets_repair.py not found")

# WhatsApp supprimé — utiliser Telegram uniquement
_wa_available = False
WA_CHANNEL = ""

# ── Structured Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ── Sentry Error Tracking (مجاني — يراقب كل الأخطاء) ─────────────────────────
try:
    import sentry_sdk
    _sentry_dsn = os.environ.get("SENTRY_DSN", "")
    if _sentry_dsn:
        sentry_sdk.init(
            dsn=_sentry_dsn,
            traces_sample_rate=0.1,
            environment="production",
        )
        logger.info("✅ Sentry Error Tracking actif")
    else:
        logger.info("ℹ️ Sentry non configuré (SENTRY_DSN manquant)")
except ImportError:
    logger.info("ℹ️ sentry-sdk non installé — pip install sentry-sdk")

bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML")

# ── Test mode ─────────────────────────────────────────────────────────────────
_test_mode: bool = config.TEST_MODE
_test_results: list[dict] = []

def is_test_mode() -> bool:
    return _test_mode

_keywords: list[str] = []
_waiting_for_keywords: set = set()
_autopilot_running: bool = False

WP_SCHEDULE_GAP = int(os.environ.get("WP_SCHEDULE_GAP_MINUTES", "60"))

# Dashboard: supprimé — utiliser /dashboard command


# ════════════════════════════════════════════════════════════════
# TELEGRAM CHANNEL POST TEMPLATE (independent, always runs)
# ════════════════════════════════════════════════════════════════

def _build_channel_post(product: dict, description: str,
                         post_url: str = "") -> str:
    """
    Beautiful channel post. Runs INDEPENDENTLY of WordPress.
    Uses all available product data.
    """
    title    = product.get("title", "")[:90]
    price    = product.get("price", "N/A")
    orig_p   = product.get("original_price", "")
    rating   = product.get("rating", 0.0)
    reviews  = product.get("review_count", 0)
    aff_link = product.get("aff_link", "")
    coupon   = product.get("coupon", "")

    # Stars
    full  = int(rating)
    half  = 1 if (rating - full) >= 0.5 else 0
    stars = "⭐" * full + ("✨" if half else "") + "☆" * (5 - full - half)

    # Rating label
    if rating >= 4.8:   rlabel = "Exceptional 🏆"
    elif rating >= 4.5: rlabel = "Excellent 🥇"
    elif rating >= 4.0: rlabel = "Very Good 👍"
    else:               rlabel = "Good"

    reviews_str = f"{reviews:,}" if reviews else "—"

    # Price block
    price_block = f"💵  <b>{price}</b>"
    if orig_p:
        price_block += f"  <s>{orig_p}</s>"
    try:
        cp = float(re.sub(r"[^\d.]", "", price))
        op = float(re.sub(r"[^\d.]", "", orig_p)) if orig_p else 0
        if op > cp > 0:
            pct = int(round((op - cp) / op * 100))
            price_block += f"  🔥 <b>-{pct}% OFF</b>"
    except Exception:
        pass

    # Coupon block
    coupon_block = ""
    if coupon:
        coupon_block = f"\n🏷️  <b>COUPON:</b>  <code>{coupon.upper()}</code>"

    # Article link
    article_block = ""
    if post_url:
        article_block = f"\n📖  <a href=\"{post_url}\">Read full review →</a>"

    return (
        f"╔══════════════════════╗\n"
        f"   🛍️  <b>AMAZON DEAL</b>\n"
        f"╚══════════════════════╝\n\n"
        f"<b>{title}</b>\n\n"
        f"<i>{description[:250]}</i>\n\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"{price_block}\n"
        f"⭐  {stars}  <b>{rating}/5</b>\n"
        f"📊  {rlabel}  •  {reviews_str} reviews"
        f"{coupon_block}\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
        f"⚡ <b>Limited stock — act fast!</b>"
        f"{article_block}\n\n"
        f"<a href=\"{aff_link}\">🛒  BUY NOW ON AMAZON  →</a>"
    )


def post_to_channel(product: dict, description: str,
                     img_bytes: bytes | None = None,
                     img_url: str = "",
                     post_url: str = "") -> bool:
    """Post to Telegram channel. INDEPENDENT of WordPress."""
    if not config.CHANNEL_ID:
        logger.warning("[channel] CHANNEL_ID not set")
        return False

    caption = _build_channel_post(product, description, post_url)

    try:
        if img_bytes:
            bot.send_photo(config.CHANNEL_ID, img_bytes, caption=caption)
        elif img_url and img_url.startswith("http"):
            bot.send_photo(config.CHANNEL_ID, img_url, caption=caption)
        else:
            bot.send_message(config.CHANNEL_ID, caption,
                             disable_web_page_preview=False)
        logger.info(f"[channel] ✓ Posted: {product.get('title','')[:50]}")
        return True
    except Exception as e:
        logger.error(f"[channel] Post error: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ════════════════════════════════════════════════════════════════

def pipeline(url: str, chat_id: int, schedule_offset: int = 0):
    """
    Amazon Affiliate Pipeline — Step A to G.
    Clean step-by-step display. No Canvas, no Sheets.
    """
    def _msg(text):
        """Send pipeline message."""
        try:
            bot.send_message(chat_id, text,
                parse_mode="HTML",
                disable_web_page_preview=True)
        except Exception:
            pass

    def _short_url(url, n=45):
        return url[:n] + "…" if len(url) > n else url

    try:
        clean_url = scraper.build_clean_url(url)

        # ══ Step A — Detected ════════════════════════════════════
        bot.send_chat_action(chat_id, "typing")
        _msg(
            f"🛒 <b>AMAZON PIPELINE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 <b>Step A</b> — Amazon link detected\n"
            f"   📎 <code>{clean_url[:70]}</code>\n"
            f"   ⏳ Starting pipeline…"
        )

        # ══ Step B — Scraping ════════════════════════════════════
        _msg("🕷️ <b>Step B</b> — Scraping Amazon…")
        product = scraper.scrape_product(url)

        if not product:
            reason = scraper.last_scrape_error or "Unknown"
            _msg(
                f"🕷️ <b>Step B</b> — Scraping Amazon\n"
                f"   ❌ <b>Failed</b>\n"
                f"   Reason: <i>{reason[:120]}</i>\n"
                f"   💡 Wait 2-3 min or try another link."
            )
            return

        # Duplicate check via Redis dedup (no Sheets)
        try:
            from ai.dedup import SemanticDeduplicator
            _dedup = SemanticDeduplicator()
            if _dedup.is_duplicate(product["title"]):
                _msg(
                    f"⚠️ <b>Duplicate detected</b>\n"
                    f"   <code>{product['title'][:60]}</code>\n"
                    f"   Already published recently — skipping."
                )
                return
        except Exception:
            pass

        full  = int(float(product.get("rating", 0)))
        stars = "⭐" * full + "☆" * max(0, 5 - full)
        orig  = (f"  ~~{product['original_price']}~~"
                 if product.get("original_price") else "")
        coupon = product.get("coupon", "") or ""

        _msg(
            f"🕷️ <b>Step B</b> — Scraping Amazon\n"
            f"   ✅ <b>Scraped!</b>\n"
            f"   ━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"   📦 <b>{product['title'][:65]}</b>\n"
            f"   💵 <b>{product['price']}</b>{orig}\n"
            f"   {stars} {product.get('rating','?')}/5"
            f" · {product.get('review_count',0):,} reviews\n"
            + (f"   🏷️ Coupon: <b>{coupon}</b>\n" if coupon else "")
            + f"   ━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        # ══ Step C — AI Description ══════════════════════════════
        _msg("🤖 <b>Step C</b> — AI description…")
        description = content_generator.generate_description(
            product["title"], product["price"]
        )
        board    = content_generator.map_pinterest_board(product["title"])
        keywords = content_generator.extract_keywords(product["title"])

        _msg(
            f"🤖 <b>Step C</b> — AI Description\n"
            f"   ✅ <b>Ready</b>\n"
            f"   📝 {len(description.split())} words"
            + (f" · 📌 Board: <code>{board}</code>" if board else "")
        )

        # ══ Step D — Telegram Channel ════════════════════════════
        _msg(f"📢 <b>Step D</b> — Posting to Telegram…")
        channel_ok = post_to_channel(
            product=product, description=description,
            img_url=product.get("img_url", ""),
        )
        channel_id = getattr(config, "CHANNEL_ID", "") or os.environ.get("CHANNEL_ID", "")

        if channel_ok:
            _msg(
                f"📢 <b>Step D</b> — Telegram Channel\n"
                f"   ✅ <b>Posted!</b>\n"
                f"   📣 Channel: <b>{channel_id}</b>"
            )
        else:
            _msg(
                f"📢 <b>Step D</b> — Telegram Channel\n"
                f"   ❌ <b>Failed</b>\n"
                f"   Reason: CHANNEL_ID not set or bot not admin\n"
                f"   ⏩ Continuing pipeline…"
            )

        # ══ Step E — WordPress ═══════════════════════════════════
        _msg("🌐 <b>Step E</b> — Publishing to WordPress…")
        wp_result  = {}
        wp_success = False
        post_url   = ""
        amazon_img = None

        if not wordpress_publisher.WP_URL:
            _msg(
                f"🌐 <b>Step E</b> — WordPress\n"
                f"   ⏭️ <b>Skipped</b> — not configured\n"
                f"   💡 Set WP_URL in .env to enable"
            )
            try:
                amazon_img = image_processor.download_image(product.get("img_url", ""))
            except Exception:
                pass
        else:
            amazon_img = wordpress_publisher.download_amazon_image(product.get("img_url", ""))
            if not amazon_img:
                amazon_img = image_processor.process_product_image(product.get("img_url", ""))

            wp_result = wordpress_publisher.publish_post(
                product=product, description=description,
                amazon_img_bytes=amazon_img or b"",
                publish_now=(schedule_offset == 0),
            )

            if wp_result.get("error"):
                _msg(
                    f"🌐 <b>Step E</b> — WordPress\n"
                    f"   ❌ <b>Failed</b>\n"
                    f"   Reason: <code>{wp_result['error'][:100]}</code>"
                )
            else:
                wp_success = True
                post_url   = wp_result.get("post_url", "")
                status     = wp_result.get("status", "publish")
                sched      = wp_result.get("scheduled", "")[:16]
                icon       = "✅" if status == "publish" else "🕐"
                _msg(
                    f"🌐 <b>Step E</b> — WordPress\n"
                    f"   {icon} <b>{'Published!' if status == 'publish' else f'Scheduled {sched}'}</b>\n"
                    + (f"   🔗 <a href=\"{post_url}\">{_short_url(post_url)}</a>" if post_url else "")
                )
                if config.CHANNEL_ID and post_url:
                    post_to_channel(product=product, description=description,
                                    img_url=product.get("img_url",""), post_url=post_url)

        # ══ Step F — Blogger ════════════════════════════════════
        blogger_url     = ""
        blogger_success = False
        _msg("📰 <b>Step F</b> — Publishing to Blogger…")

        try:
            if blogger_api_publisher.is_configured():
                b_result = blogger_api_publisher.publish_post(
                    product=product, description=description,
                    labels=[board] if board else [], publish_now=True,
                )
                if b_result.get("error"):
                    err = b_result['error'][:100]
                    if "429" in err or "rateLimitExceeded" in err:
                        _msg(
                            f"📰 <b>Step F</b> — Blogger\n"
                            f"   ⏳ <b>Rate limited</b> — quota exhausted (resets tomorrow)\n"
                            f"   ✅ Telegram post published successfully"
                        )
                    else:
                        _msg(
                            f"📰 <b>Step F</b> — Blogger\n"
                            f"   ❌ <b>Failed</b>\n"
                            f"   Reason: <code>{err}</code>"
                        )
                else:
                    blogger_success = True
                    blogger_url     = b_result.get("post_url", "")
                    _msg(
                        f"📰 <b>Step F</b> — Blogger (API v3)\n"
                        f"   ✅ <b>Published!</b>\n"
                        + (f"   🔗 <a href=\"{blogger_url}\">{_short_url(blogger_url)}</a>" if blogger_url else "")
                    )
            else:
                _msg(
                    f"📰 <b>Step F</b> — Blogger\n"
                    f"   ⏭️ <b>Skipped</b> — not configured\n"
                    f"   💡 Add BLOGGER_* variables in .env"
                )
        except Exception as _be:
            _msg(f"📰 <b>Step F</b> — Blogger\n   ❌ Error: <code>{str(_be)[:80]}</code>")
            logger.warning(f"[pipeline] Blogger error: {_be}")

        best_article_url = blogger_url or post_url or product.get("aff_link", "")

        # ══ Step G — Pinterest (py3-pinterest auto) ═════════════════
        _msg("📌 <b>Step G</b> — Publishing to Pinterest…")
        pinterest_ok  = False
        pinterest_url = ""

        try:
            pin_email = os.environ.get("PINTEREST_EMAIL", "")
            pin_pass  = os.environ.get("PINTEREST_PASSWORD", "")
            pin_token = os.environ.get("PINTEREST_ACCESS_TOKEN", "")

            _category  = product.get("category","shopping").replace(" ","").lower()
            _hashtags  = f"#amazon #deals #{_category} #affiliate"
            _pin_title = product["title"][:100]
            _pin_desc  = f"{description[:350]}\n\n{_hashtags}"
            _pin_img   = product.get("img_url","")
            _pin_link  = best_article_url or product.get("aff_link","")

            if pin_email and pin_pass:
                # ── Playwright browser automation (most reliable, no API) ─────
                _msg("   🌐 Launching headless browser…")
                try:
                    from pinterest.playwright_publisher import publish_pin_sync
                    _pr = publish_pin_sync(
                        title       = _pin_title,
                        description = _pin_desc,
                        image_url   = _pin_img,
                        link        = _pin_link,
                    )
                    if _pr.get("success"):
                        pinterest_ok  = True
                        pinterest_url = _pr.get("pin_url","")
                        board_name    = _pr.get("board","Auto")
                        _msg(
                            f"📌 <b>Step G</b> — Pinterest (Browser)\n"
                            f"   ✅ <b>Pin published!</b>\n"
                            f"   🎯 Board: <b>{board_name}</b>"
                            + (f"\n   🔗 <a href=\"{pinterest_url}\">View Pin</a>" if pinterest_url else "")
                        )
                    else:
                        raise Exception(_pr.get("error","browser publish failed"))
                except Exception as _pwe:
                    logger.warning(f"[pipeline] playwright pinterest: {_pwe}")
                    _msg(
                        f"📌 <b>Step G</b> — Pinterest (Browser)\n"
                        f"   ❌ <b>Failed</b>\n"
                        f"   <code>{str(_pwe)[:110]}</code>\n"
                        f"   💡 Check PINTEREST_EMAIL + PINTEREST_PASSWORD\n"
                        f"   💡 Disable 2FA on Pinterest account"
                    )

            elif pin_token:
                # ── Official API — new event loop pour éviter le thread error ─
                import asyncio as _aio
                try:
                    from pinterest.auto_board import publish_pin as _pin_pub
                    # Créer un nouveau event loop (fix "no current event loop in thread")
                    _loop = _aio.new_event_loop()
                    try:
                        _pr = _loop.run_until_complete(
                            _pin_pub(
                                token       = pin_token,
                                title       = _pin_title,
                                description = _pin_desc,
                                image_url   = _pin_img,
                                link        = _pin_link,
                            )
                        )
                    finally:
                        _loop.close()

                    if _pr.get("success"):
                        pinterest_ok  = True
                        pinterest_url = _pr.get("pin_url","")
                        _msg(
                            f"📌 <b>Step G</b> — Pinterest\n"
                            f"   ✅ <b>Pin created!</b>\n"
                            f"   🎯 Board: {_pr.get('board','Auto')}\n"
                            + (f"   🔗 <a href=\"{pinterest_url}\">View Pin</a>" if pinterest_url else "")
                        )
                    else:
                        err = _pr.get("error","")
                        tip = "\n   💡 Request Standard Access at developers.pinterest.com" if "401" in err else ""
                        _msg(f"📌 <b>Step G</b> — Pinterest\n   ❌ {err[:100]}{tip}")
                except Exception as _ae:
                    _msg(f"📌 <b>Step G</b> — Pinterest\n   ❌ <code>{str(_ae)[:80]}</code>")
            else:
                _msg(
                    f"📌 <b>Step G</b> — Pinterest\n"
                    f"   ⏭️ <b>Skipped</b> — not configured\n"
                    f"   💡 Add to .env:\n"
                    f"   PINTEREST_EMAIL + PINTEREST_PASSWORD\n"
                    f"   (no Standard Access needed)"
                )

        except Exception as _pe:
            _msg(f"📌 <b>Step G</b> — Pinterest\n   ❌ Error: <code>{str(_pe)[:80]}</code>")
            logger.warning(f"[pipeline] Pinterest error: {_pe}")

        # Add to queue
        try:
            queue_manager.add_product(
                product, product.get("img_url",""), description, keywords, board
            )
        except Exception:
            pass

        # ══ FINAL SUMMARY ════════════════════════════════════════
        def _st(ok): return "✅" if ok else "❌"
        queue_n = queue_manager.pending_count()

        _msg(
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 <b>PIPELINE COMPLETE!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 D — Telegram:   {_st(channel_ok)} {channel_id}\n"
            f"🌐 E — WordPress:  {_st(wp_success)}"
            + (f" <a href=\"{post_url}\">view</a>" if wp_success and post_url else "") + "\n"
            f"📰 F — Blogger:    {_st(blogger_success)}"
            + (f" <a href=\"{blogger_url}\">view</a>" if blogger_success and blogger_url else "") + "\n"
            f"📌 G — Pinterest:  {_st(pinterest_ok)}"
            + (f" <a href=\"{pinterest_url}\">view</a>" if pinterest_ok and pinterest_url else "") + "\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📬 Queue: <b>{queue_n} items pending</b>"
        )

    except Exception as e:
        logger.error(f"[pipeline] {e}", exc_info=True)
        bot.send_message(chat_id,
            f"❌  <b>Unexpected error</b>\n<code>{e}</code>")


# ════════════════════════════════════════════════════════════════
# SCHEDULER CALLBACK
# ════════════════════════════════════════════════════════════════

def _scheduler_post(item: dict):
    product = {k: item.get(k, "") for k in
               ["title","price","original_price","rating","review_count",
                "aff_link","coupon","img_url"]}
    product["rating"] = float(product.get("rating") or 0)
    product["review_count"] = int(product.get("review_count") or 0)
    post_to_channel(
        product     = product,
        description = item.get("description", ""),
        img_url     = item.get("media_url", "") or item.get("img_url", ""),
        post_url    = item.get("aff_link", ""),
    )


scheduler.set_post_callback(_scheduler_post)


# ════════════════════════════════════════════════════════════════
# COMMANDS
# ════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["start", "help"])
def cmd_help(message):
    test_badge = " 🧪 TEST" if is_test_mode() else ""
    msg = (
        f"╔══════════════════════════════╗\n"
        f"   🤖  <b>Amazon Affiliate Bot v4{test_badge}</b>\n"
        f"╚══════════════════════════════╝\n\n"
        "🎛 /dashboard — <b>Panneau de contrôle complet</b>\n\n"

        "━━━━ 📦 AMAZON (Produits) ━━━━\n"
        "  Envoie un lien Amazon → pipeline auto\n"
        "  /batch              — publier par lot (.txt)\n\n"

        "  /testmode on|off    — mode test\n"
        "  /deletetest         — supprimer données test\n\n"



        "━━━━ 📌 PINTEREST ━━━━\n"
        "  /pinterestcsv       — générer CSV (planifié)\n"
        "  /pincheck           — vérifier avant export\n"
        "  /pinfix [apply]     — corriger les boards\n"
        "  /pinaudit           — audit complet\n"
        "  /pinrepair [all]    — réparer médias/titres\n"
        "  /pinmarkdone        — formater publiés\n"
        "  /pinstatus          — statistiques\n"
        "  /pinpublish [N]     — publier via API\n"
        "  /startpinterest     — démarrer auto\n"
        "  /stoppinterest      — arrêter auto\n\n"

        "━━━━ 🌐 WORDPRESS ━━━━\n"
        "  /wpposts            — posts récents\n"
        "  /wpdelete [ID]      — supprimer un post\n"
        "  /testwp             — tester connexion\n"
        "  /wp_debug           — debug variables\n\n"

        "━━━━ 📝 BLOGGER ━━━━\n"
        "  /blogposts          — posts récents\n"
        "  /blogdelete [ID]    — supprimer un post\n"
        "  /auth_blogger       — authentification OAuth\n"
        "  /blogger_code [c]   — code OAuth\n"
        "  /test_blogger       — tester connexion\n\n"





        "━━━━ ⏰ SCHEDULER ━━━━\n"
        "  /startautopost      — démarrer autopost\n"
        "  /stopautopost       — arrêter\n"
        "  /setinterval [N]    — intervalle (min)\n"
        "  /queue              — file d'attente\n"
        "  /clearqueue         — vider\n\n"

        "━━━━ 🤖 AUTOPILOT ━━━━\n"
        "  /loadkeywords       — charger mots-clés\n"
        "  /autopilot          — lancer\n"
        "  /stopautopilot      — arrêter\n\n"


        "━━━━ 🎨 IMAGES ━━━━\n"
        "  /testimage [url]    — tester transformation\n"
        "  (transforme les images Amazon pour\n"
        "   éviter les problèmes de copyright)\n\n"

        "━━━━ 📊 ANALYTICS & MONITORING ━━━━\n"
        "  /dashboard          — tableau de bord complet\n"
        "  (métriques, publications, Pinterest, TinyPNG)\n"
        "\n"
        "━━━━ ⚙️ STATUTS & DEBUG ━━━━\n"
        "  /status             — état général\n"
        "  /health             — 🏥 santé de tous les services\n"
        "  /backup             — 📦 sauvegarde manuelle\n"
        "  /autostatus         — 🤖 état découverte automatique\n"
        "  /platformstatus     — toutes les plateformes\n"
        "\n"
        "━━━━ 🤖 IA ACTIVE ━━━━\n"
        "  Providers: Groq → Gemini → OpenRouter → Template\n"
        "  SEO auto: FAQ + Schema.org + meta tags\n"
        "  Pinterest: 3 variants/article (A/B/C)\n"
        "  TinyPNG: compression images auto\n"
    )
    bot.reply_to(message, msg, parse_mode="HTML")


@bot.message_handler(commands=["status"])
def cmd_status(message):
    running = scheduler.is_running()
    wp_ok   = "✓" if wordpress_publisher.test_connection() else "✗ not configured"
    bot.reply_to(message,
        f"📊  <b>Bot Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢' if running else '🔴'}  Auto-post scheduler\n"
        f"{'🟢' if _autopilot_running else '🔴'}  Autopilot\n"
        f"⏱  Interval: <b>{scheduler.get_interval()} min</b>\n"
        f"📬  Queue: <b>{queue_manager.pending_count()}</b>\n"
        f"🔑  Keywords loaded: <b>{len(_keywords)}</b>\n"
        f"🌐  WordPress: <b>{wp_ok}</b>\n"
        f"📢  Channel: <code>{config.CHANNEL_ID or 'not set'}</code>\n"
        f"🏷  Affiliate tag: <code>{config.AFFILIATE_TAG}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ════════════════════════════════════════════════════════════════
# /health — فحص حالة كل الخدمات دفعة واحدة
# ════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["health"])
def cmd_health(message):
    """
    يفحص كل خدمة ويعطي إجابة خضراء أو حمراء.
    مفيد للمطور والزبون لمعرفة أن كل شيء يعمل.
    """
    bot.send_chat_action(message.chat.id, "typing")
    checks = {}

    # Redis
    try:
        from core.safe_redis import get_safe_redis
        r = get_safe_redis()
        checks["⚡ Redis"] = "✅ Online" if r else "❌ Offline"
    except Exception:
        checks["⚡ Redis"] = "⚠️ Non configuré"

    # PostgreSQL
    try:
        db_url = os.environ.get("DATABASE_URL", "")
        checks["🗄 PostgreSQL"] = "✅ Online" if db_url else "⚠️ Non configuré"
    except Exception:
        checks["🗄 PostgreSQL"] = "❌ Erreur"

    # Groq AI
    checks["🤖 Groq AI"] = (
        "✅ Configuré" if os.environ.get("GROQ_API_KEY") else "⚠️ GROQ_API_KEY manquant"
    )

    # Gemini AI
    checks["✨ Gemini AI"] = (
        "✅ Configuré" if os.environ.get("GEMINI_API_KEY") else "⚠️ GEMINI_API_KEY manquant"
    )

    # WordPress
    try:
        wp_ok = wordpress_publisher.test_connection()
        checks["📝 WordPress"] = "✅ Connecté" if wp_ok else "❌ Échec connexion"
    except Exception:
        checks["📝 WordPress"] = "⚠️ Non configuré"

    # Blogger
    try:
        bl_ok = bool(os.environ.get("BLOGGER_BLOG_ID") and os.environ.get("BLOGGER_REFRESH_TOKEN"))
        checks["📰 Blogger"] = "✅ Configuré" if bl_ok else "⚠️ Non configuré"
    except Exception:
        checks["📰 Blogger"] = "⚠️ Non configuré"

    # Pinterest
    try:
        pin_ok = bool(os.environ.get("PINTEREST_ACCESS_TOKEN"))
        checks["📌 Pinterest"] = "✅ Configuré" if pin_ok else "⚠️ Non configuré"
    except Exception:
        checks["📌 Pinterest"] = "⚠️ Non configuré"

    # TinyPNG
    checks["🖼 TinyPNG"] = (
        "✅ Configuré" if os.environ.get("TINIFY_API_KEY") else "⚠️ Non configuré"
    )

    # Sentry
    checks["🔭 Sentry"] = (
        "✅ Actif" if os.environ.get("SENTRY_DSN") else "⚠️ Non configuré"
    )

    # Résumé
    all_ok    = all("✅" in v for v in checks.values())
    has_warn  = any("⚠️" in v for v in checks.values())
    has_error = any("❌" in v for v in checks.values())

    if all_ok:
        overall = "🟢 <b>Tout fonctionne parfaitement</b>"
    elif has_error:
        overall = "🔴 <b>Problèmes détectés</b>"
    else:
        overall = "🟡 <b>Fonctionne (certains services non configurés)</b>"

    lines = [
        "🏥 <b>System Health Check</b>",
        f"<code>{datetime.now().strftime('%d/%m/%Y %H:%M')}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for name, status in checks.items():
        lines.append(f"  {status}  {name}")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        overall,
    ]
    bot.reply_to(message, "\n".join(lines), parse_mode="HTML")


# ════════════════════════════════════════════════════════════════
# /backup — نسخ احتياطي فوري لقاعدة البيانات
# ════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["autostatus", "autodiscover"])
def cmd_auto_status(message):
    """حالة الـ Daily Scheduler التلقائي."""
    try:
        from daily_scheduler import is_running
        running  = is_running()
        enabled  = os.environ.get("AUTO_DISCOVER_ENABLED", "false")
        hour     = os.environ.get("AUTO_DISCOVER_HOUR", "8")
        n        = os.environ.get("AUTO_ARTICLES_PER_DAY", "5")
        auto_pub = os.environ.get("AUTO_PUBLISH_MODE", "false")
        sites    = os.environ.get("AUTO_DISCOVER_SITES", "sites par défaut")

        status = "🟢 En cours" if running else "🔴 Arrêté"
        msg = (
            f"🤖 <b>Daily Scheduler</b>\n"
            f"{'━'*24}\n"
            f"État:      {status}\n"
            f"Activé:    {enabled}\n"
            f"Heure:     {hour}h00\n"
            f"Articles:  {n}/jour\n"
            f"Mode:      {'Auto 🚀' if auto_pub == 'true' else '👤 Manuel'}\n"
            f"Sites:     {sites[:80]}\n"
            f"{'━'*24}\n"
            f"Variables .env:\n"
            f"  AUTO_DISCOVER_ENABLED\n"
            f"  AUTO_DISCOVER_HOUR\n"
            f"  AUTO_ARTICLES_PER_DAY\n"
            f"  AUTO_PUBLISH_MODE"
        )
        bot.reply_to(message, msg, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Erreur: {e}")


@bot.message_handler(commands=["backup"])
def cmd_backup(message):
    """
    يرسل ملف JSON بكل إعدادات البوت كنسخة احتياطية.
    آمن — لا يحتوي على API keys.
    """
    bot.send_chat_action(message.chat.id, "upload_document")
    try:
        import json, io

        backup_data = {
            "timestamp":      datetime.now().isoformat(),
            "bot_version":    "v4",
            "queue_count":    queue_manager.pending_count(),
            "keywords_count": len(_keywords),
            "test_mode":      _test_mode,
            "services": {
                "wordpress":  bool(os.environ.get("WP_SITE_URL")),
                "blogger":    bool(os.environ.get("BLOGGER_BLOG_ID")),
                "pinterest":  bool(os.environ.get("PINTEREST_ACCESS_TOKEN")),
                "groq":       bool(os.environ.get("GROQ_API_KEY")),
                "gemini":     bool(os.environ.get("GEMINI_API_KEY")),
            },
        }

        # محاولة إضافة بيانات Redis
        try:
            from core.safe_redis import safe_get
            fb_stats = safe_get("feedback:stats:global")
            if fb_stats:
                backup_data["ai_stats"] = json.loads(fb_stats)
        except Exception:
            pass

        content_bytes = json.dumps(backup_data, ensure_ascii=False, indent=2).encode("utf-8")
        filename      = f"backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json"

        bot.send_document(
            message.chat.id,
            io.BytesIO(content_bytes),
            visible_file_name=filename,
            caption=(
                f"📦 <b>Backup AutoAffiliate Pro</b>\n"
                f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
                f"✅ Sauvegardez ce fichier en lieu sûr"
            ),
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Backup échoué: <code>{e}</code>")



@bot.message_handler(commands=["testwp"])
def cmd_testwp(message):
    bot.reply_to(message, "🔗  Testing WordPress…")
    ok = wordpress_publisher.test_connection()
    bot.send_message(message.chat.id,
        f"{'✅' if ok else '❌'}  WordPress: "
        f"{'connected' if ok else 'failed — check WP_URL, WP_USERNAME, WP_APP_PASSWORD'}\n"
        f"<code>{wordpress_publisher.WP_URL or 'not set'}</code>"
    )


# ════════════════════════════════════════════════════════════════
# BLOGGER AUTH COMMANDS
# ════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["auth_blogger"])
def cmd_auth_blogger(message):
    """Step 1 — Send OAuth URL to user."""
    import blogger_publisher
    if not blogger_publisher.CLIENT_ID or not blogger_publisher.CLIENT_SECRET:
        bot.reply_to(message,
            "❌  <b>Blogger not configured</b>\n\n"
            "Add these to .env:\n"
            "<code>BLOGGER_CLIENT_ID</code>\n"
            "<code>BLOGGER_CLIENT_SECRET</code>"
        )
        return

    url = blogger_publisher.get_auth_url()
    bot.reply_to(message,
        "🔐  <b>Blogger Authorization — 3 Steps</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Step 1:</b> Click the link below\n"
        "<b>Step 2:</b> Login with Google → Allow\n"
        "<b>Step 3:</b> Browser shows an error page — that's OK!\n"
        "Copy the <code>code=</code> value from the URL bar\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 <a href='{url}'>Click here to authorize Blogger</a>\n\n"
        "After you get the code, send it like this:\n"
        "<code>/blogger_code PASTE_CODE_HERE</code>"
    )


@bot.message_handler(commands=["blogger_code"])
def cmd_blogger_code(message):
    """Step 2 — Receive code and exchange for tokens."""
    import blogger_publisher

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message,
            "❌  No code found.\n\n"
            "Usage: <code>/blogger_code YOUR_CODE_HERE</code>\n\n"
            "First run /auth_blogger to get the link."
        )
        return

    code = parts[1].strip()

    # Extract code if user pasted full URL
    import re
    url_match = re.search(r"code=([^&\s]+)", code)
    if url_match:
        code = url_match.group(1)

    bot.reply_to(message, "⏳  Exchanging code for tokens…")

    result = blogger_publisher.exchange_code(code)

    if result.get("ok"):
        # Test connection immediately
        ok = blogger_publisher.test_connection()
        bot.send_message(message.chat.id,
            "✅  <b>Blogger Connected!</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝  Blog ID: <code>{blogger_publisher.BLOG_ID}</code>\n"
            f"🔗  Connection test: {'✅ OK' if ok else '⚠️ Check blog ID'}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡  Token saved! The bot will now publish to Blogger automatically.\n\n"
            "Test with: /test_blogger"
        )
    else:
        bot.send_message(message.chat.id,
            "❌  <b>Authorization Failed</b>\n\n"
            f"Error: <code>{result.get('error', 'Unknown')}</code>\n\n"
            "Common causes:\n"
            "• Code expired (use within 1 minute)\n"
            "• Wrong redirect URI in Google Console\n"
            "  → Must be exactly: <code>http://localhost</code>\n\n"
            "Try again: /auth_blogger"
        )


@bot.message_handler(commands=["test_blogger"])
def cmd_test_blogger(message):
    """Test Blogger connection."""
    import blogger_publisher
    bot.reply_to(message, "🔗  Testing Blogger connection…")

    if not blogger_publisher.is_configured():
        bot.send_message(message.chat.id,
            "❌  Blogger not configured.\n"
            "Run /auth_blogger first."
        )
        return

    ok = blogger_publisher.test_connection()
    bot.send_message(message.chat.id,
        f"{'✅' if ok else '❌'}  Blogger: "
        f"{'Connected!' if ok else 'Failed — try /auth_blogger again'}\n"
        f"Blog ID: <code>{blogger_publisher.BLOG_ID}</code>"
    )


@bot.message_handler(commands=["queue"])
def cmd_queue(message):
    n = queue_manager.pending_count()
    bot.reply_to(message,
        f"📬  <b>{n} item(s)</b> in queue." if n
        else "📭  Queue empty.")


@bot.message_handler(commands=["testimage"])
def cmd_testimage(message):
    """Test image transformation with a sample Amazon image."""
    import re as _re
    
    # Extract URL from message
    parts = message.text.split()
    url = None
    for part in parts:
        if part.startswith("http"):
            url = part
            break
    
    if not url:
        bot.reply_to(message, 
            "Usage: /testimage <amazon_image_url>\n"
            "Example: /testimage https://m.media-amazon.com/images/I/61xxx.jpg")
        return
    
    bot.reply_to(message, "🎨 Downloading and transforming image...")
    
    try:
        from image_transformer import transform_image, _download_image, get_available_presets
        from image_processor import upload_image
        
        # Download
        original = _download_image(url)
        if not original:
            bot.send_message(message.chat.id, "❌ Could not download image")
            return
        
        # Show available presets
        presets = get_available_presets()
        preset_list = "\n".join([f"  • {k}: {v['description']}" for k, v in presets.items()])
        
        # Transform with each preset and upload
        results = []
        for preset_name in ["oil_painting", "watercolor", "soft_glow"]:
            transformed = transform_image(original, preset=preset_name, add_shadow=True)
            if transformed:
                new_url = upload_image(transformed, f"test_{preset_name}.jpg")
                if new_url:
                    results.append(f"✅ {preset_name}: {new_url[:50]}...")
        
        if results:
            bot.send_message(message.chat.id,
                f"🎨 <b>Image Transformation Test</b>\n\n"
                f"Available presets:\n{preset_list}\n\n"
                f"Results:\n" + "\n".join(results) + 
                f"\n\nAll images are unique to avoid copyright detection.")
        else:
            bot.send_message(message.chat.id, "❌ Transformation failed")
            
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {str(e)}")


@bot.message_handler(commands=["clearqueue"])
def cmd_clearqueue(message):
    queue_manager.clear_queue()
    bot.reply_to(message, "🗑  Queue cleared.")


@bot.message_handler(commands=["setinterval"])
def cmd_setinterval(message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "Usage: /setinterval 30")
        return
    scheduler.set_interval(int(parts[1]))
    bot.reply_to(message, f"⏱  Interval: <b>{parts[1]} min</b>")


@bot.message_handler(commands=["startautopost"])
def cmd_startautopost(message):
    if scheduler.is_running():
        bot.reply_to(message, "ℹ️  Already running.")
        return
    if queue_manager.pending_count() == 0:
        bot.reply_to(message, "⚠️  Queue empty. Add products first.")
        return
    scheduler.start()
    bot.reply_to(message,
        f"🚀  <b>Scheduler started!</b>\n"
        f"⏱  Every {scheduler.get_interval()} min\n"
        f"📬  {queue_manager.pending_count()} items"
    )


@bot.message_handler(commands=["stopautopost"])
def cmd_stopautopost(message):
    scheduler.stop()
    bot.reply_to(message, "🛑  Scheduler stopped.")


@bot.message_handler(commands=["loadkeywords"])
def cmd_loadkeywords(message):
    _waiting_for_keywords.add(message.chat.id)
    bot.reply_to(message,
        "📄  Send a <code>.txt</code> file — one keyword per line.")


@bot.message_handler(content_types=["document"])
def handle_document(message):
    """
    Smart document handler — routes by file type:
      .csv  → Pinterest error analysis (or Sheets export)
      .txt  → keyword loading (only when /loadkeywords was sent first)
      other → ignore
    """
    fname = (message.document.file_name or "").lower() if message.document else ""

    # ── CSV → Pinterest analysis ──────────────────────────────────────────
    if fname.endswith(".csv"):
        if not _sheets_repair_available:
            bot.reply_to(message, "⚠️ sheets_repair.py non déployé.")
            return
        try:
            bot.reply_to(message, "📎 CSV reçu — analyse Pinterest en cours…")
            file_info = bot.get_file(message.document.file_id)
            csv_bytes = bot.download_file(file_info.file_path)

            try:
                sample = csv_bytes[:300].decode("utf-8-sig")
            except Exception:
                sample = ""
            is_pinterest = any(k in sample for k in
                               ["Pinterest board","Media URL","Publish date","pin_id","Title"])
            if not is_pinterest:
                bot.send_message(message.chat.id,
                    "⚠️ Ce CSV ne semble pas être un fichier Pinterest.\n"
                    "Envoie le fichier téléchargé depuis Pinterest → Bulk Create.")
                return

            records = sheets_handler._safe_get_all_records(sheets_handler.get_sheet())
            report  = sheets_repair.analyze_csv(csv_bytes, records)
            cdn_n   = len(report["amazon_cdn"])
            sim_n   = len(report["similar_titles"])
            board_n = len(report["wrong_board"])
            total   = report["total"]

            lines = [
                f"📊 <b>Analyse CSV Pinterest ({total} lignes)</b>",
                f"━━━━━━━━━━━━━━━━━━━━━━━━",
                f"🖼 Images Amazon CDN    : <b>{cdn_n}</b>  ← cause principale des rejets",
                f"✏️ Titres quasi-dupliqués: <b>{sim_n} paires</b>",
                f"📌 Boards améliorables  : <b>{board_n}</b>",
                f"━━━━━━━━━━━━━━━━━━━━━━━━",
            ]
            if cdn_n:
                lines.append(f"\n🖼 <b>Images à re-héberger :</b>")
                for r in report["amazon_cdn"][:8]:
                    lines.append(f"  L{r['sheet_row']}: {r['title']}")
            if sim_n:
                lines.append(f"\n✏️ <b>Titres à différencier :</b>")
                for p in report["similar_titles"][:5]:
                    lines.append(f"  L{p['row_a']} ↔ L{p['row_b']}: [{p['variant_a']}] vs [{p['variant_b']}]")
            lines += [
                f"\n🚀 Tout corriger : <code>/pinrepair all</code>",
                f"/pinrepair media   ({cdn_n} images)",
                f"/pinrepair titles  ({sim_n} paires)",
                f"/pinrepair boards  ({board_n} boards)",
            ]
            from pinterest_csv_exporter import _send_chunks
            _send_chunks(bot, message.chat.id, lines)
        except Exception as e:
            logger.error(f"[doc_handler] CSV error: {e}")
            bot.send_message(message.chat.id, f"❌ Erreur analyse CSV: <code>{e}</code>")
        return

    # ── TXT → keywords loading ────────────────────────────────────────────
    if fname.endswith(".txt"):
        # Check if this is a batch publish request
        if message.chat.id in _waiting_for_batch:
            _waiting_for_batch.discard(message.chat.id)
            try:
                fi  = bot.get_file(message.document.file_id)
                raw = bot.download_file(fi.file_path)
                keywords = [l.strip() for l in raw.decode("utf-8").splitlines() if l.strip()]
                if not keywords:
                    bot.reply_to(message, "❌ File is empty or has no keywords.")
                    return
                bot.reply_to(message, f"📥 Received {len(keywords)} keywords. Starting batch publish...")
                _run_batch_publish(bot, message.chat.id, keywords)
            except Exception as e:
                bot.reply_to(message, f"❌ Error: {e}")
            return
        
        if message.chat.id not in _waiting_for_keywords:
            bot.reply_to(message, "💡 Pour charger des mots-clés, envoie /loadkeywords d\'abord.")
            return
        _waiting_for_keywords.discard(message.chat.id)
        try:
            fi  = bot.get_file(message.document.file_id)
            raw = bot.download_file(fi.file_path)
            global _keywords
            _keywords = [l.strip() for l in raw.decode("utf-8").splitlines() if l.strip()]
            prev  = ", ".join(_keywords[:5])
            extra = f" …+{len(_keywords)-5} more" if len(_keywords) > 5 else ""
            bot.reply_to(message,
                f"✅  <b>{len(_keywords)} keywords loaded!</b>\n"
                f"<code>{prev}{extra}</code>\n\nUse /autopilot to start."
            )
        except Exception as e:
            bot.reply_to(message, f"❌  {e}")
        return

    # ── Other file types — ignore ─────────────────────────────────────────
    bot.reply_to(message, "📎 Fichier reçu. Envoie un .csv Pinterest ou un .txt de mots-clés.")


@bot.message_handler(commands=["stopautopilot"])
def cmd_stopautopilot(message):
    global _autopilot_running
    _autopilot_running = False
    bot.reply_to(message, "🛑  Stopping after current product.")


# ── Batch publish from text file ─────────────────────────────────────────────
_waiting_for_batch = set()

@bot.message_handler(commands=["batch"])
def cmd_batch(message):
    """
    Batch publish: send a .txt file with one product keyword per line.
    Bot will search Amazon for each keyword and publish ONE product per keyword.
    """
    _waiting_for_batch.add(message.chat.id)
    bot.reply_to(message,
        "📥 <b>Batch Publish Mode</b>\n\n"
        "Send a .txt file with one product keyword per line.\n"
        "Example:\n"
        "<code>wireless earbuds\n"
        "phone case iphone 15\n"
        "usb c hub\n"
        "laptop stand</code>\n\n"
        "The bot will:\n"
        "• Search Amazon for each keyword\n"
        "• Get ONE product per keyword\n"
        "• Transform image to avoid copyright\n"
        "• Publish to Blogger\n\n"
        "Send your .txt file now..."
    )


def _run_batch_publish(bot_instance, chat_id: int, keywords: list[str]):
    """
    Process batch of keywords: search Amazon for each, get ONE product, publish.
    """
    import scraper as _scraper_mod
    import blogger_api_publisher as _blogger_mod
    import content_generator as _cg_mod
    from image_transformer import transform_image, _download_image
    from image_processor import upload_image
    
    total = len(keywords)
    success = 0
    failed = 0
    
    bot_instance.send_message(chat_id,
        f"🚀 <b>Batch Publish Started</b>\n"
        f"📊 {total} keywords to process\n"
        f"⏱ Estimated time: {total * 30}-{total * 60} seconds\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    for i, keyword in enumerate(keywords, 1):
        try:
            # Progress update every 5 keywords
            if i % 5 == 1 or i == total:
                bot_instance.send_message(chat_id,
                    f"📊 <b>Progress: {i}/{total}</b>\n"
                    f"✅ Success: {success} | ❌ Failed: {failed}"
                )
            
            bot_instance.send_message(chat_id,
                f"🔍 [{i}/{total}] Searching: <code>{keyword}</code>"
            )
            
            # Search Amazon for this keyword - get ONE product
            results = _scraper_mod.search_amazon(
                keyword=keyword,
                max_results=3,  # Get top 3 to have fallbacks
                min_rating=3.5,
                min_reviews=10,
            )
            
            if not results:
                # Try broader search
                results = _scraper_mod.search_amazon(
                    keyword=keyword,
                    max_results=5,
                    min_rating=3.0,
                    min_reviews=5,
                )
            
            if not results:
                bot_instance.send_message(chat_id,
                    f"   ❌ No products found for: <code>{keyword}</code>"
                )
                failed += 1
                continue
            
            # Get the best product (first one, already sorted by value)
            product = results[0]
            
            # Ensure affiliate link
            if not product.get("aff_link"):
                product["aff_link"] = _scraper_mod.build_affiliate_url(product.get("url", ""))
            
            # Transform image
            img_url = product.get("img_url", "")
            if img_url:
                try:
                    original_bytes = _download_image(img_url)
                    if original_bytes:
                        import random
                        preset = random.choice(["oil_painting", "watercolor", "soft_glow"])
                        transformed = transform_image(original_bytes, preset=preset, add_shadow=True)
                        if transformed:
                            safe_name = f"batch_{i}.jpg"
                            new_url = upload_image(transformed, safe_name)
                            if new_url:
                                product["img_url"] = new_url
                except Exception as e:
                    logger.warning(f"[batch] Image transform failed: {e}")
            
            # Generate AI description
            description = _cg_mod.generate_description(
                product["title"], product["price"]
            )
            
            # Publish to Blogger
            blogger_ok = False
            blogger_url = ""
            
            if _blogger_mod.is_configured():
                try:
                    board = _cg_mod.map_pinterest_board(product["title"])
                    b_result = _blogger_mod.publish_post(
                        product=product,
                        description=description,
                        labels=[board] if board else [],
                        publish_now=True,
                    )
                    if not b_result.get("error"):
                        blogger_ok = True
                        blogger_url = b_result.get("post_url", "")
                except Exception as e:
                    logger.error(f"[batch] Blogger error: {e}")
            
            if blogger_ok:
                success += 1
                bot_instance.send_message(chat_id,
                    f"   ✅ <b>Published!</b>\n"
                    f"   📦 {product['title'][:50]}...\n"
                    f"   💵 {product.get('price', 'N/A')}\n"
                    + (f"   🔗 <a href=\"{blogger_url}\">View Post</a>" if blogger_url else "")
                )
            else:
                failed += 1
                bot_instance.send_message(chat_id,
                    f"   ⚠️ Scraped but publish failed\n"
                    f"   📦 {product['title'][:50]}..."
                )
            
            # Delay between products to avoid rate limiting
            import time
            time.sleep(3)
            
        except Exception as e:
            failed += 1
            logger.error(f"[batch] Error processing keyword '{keyword}': {e}")
            bot_instance.send_message(chat_id,
                f"   ❌ Error: <code>{str(e)[:80]}</code>"
            )
    
    # Final summary
    bot_instance.send_message(chat_id,
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 <b>Batch Complete!</b>\n\n"
        f"📊 Total: {total}\n"
        f"✅ Success: {success}\n"
        f"❌ Failed: {failed}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )


@bot.message_handler(commands=["autopilot"])
def cmd_autopilot(message):
    global _autopilot_running
    if not _keywords:
        bot.reply_to(message, "⚠️  No keywords. Use /loadkeywords first.")
        return
    if _autopilot_running:
        bot.reply_to(message, "⚠️  Already running. /stopautopilot to stop.")
        return

    _autopilot_running = True
    sched_offset = 0
    bot.reply_to(message,
        f"🤖  <b>Autopilot started</b>\n"
        f"{len(_keywords)} keywords — /stopautopilot to stop"
    )
    total = 0
    for i, kw in enumerate(_keywords, 1):
        if not _autopilot_running:
            bot.send_message(message.chat.id,
                f"🛑  Stopped. {total} products processed.")
            return
        bot.send_message(message.chat.id,
            f"🔍  [{i}/{len(_keywords)}]  <b>{kw}</b>")
        try:
            products = scraper.search_amazon(kw, max_results=config.MAX_RESULTS)
            added = 0
            for p in products:
                if not _autopilot_running:
                    break
                if sheets_handler.is_duplicate(p["asin"]):
                    continue
                pipeline(p["clean_url"], message.chat.id,
                         schedule_offset=sched_offset)
                sched_offset += WP_SCHEDULE_GAP
                added += 1
                total += 1
                time.sleep(5)
            bot.send_message(message.chat.id,
                f"   ✅  {added} products from <i>{kw}</i>")
        except Exception as e:
            bot.send_message(message.chat.id,
                f"   ⚠️  <i>{kw}</i>: <code>{e}</code>")
        time.sleep(3)

    _autopilot_running = False
    bot.send_message(message.chat.id,
        f"🎉  <b>Autopilot done!</b>  {total} products processed.")


def _canvas_status(card_bytes: bytes | None, canvas_url: str, amazon_url: str) -> str:
    if not card_bytes:
        return "❌ Render failed (template.png manquant ?)"
    if canvas_url and canvas_url != amazon_url:
        if "ibb.co"      in canvas_url: return "✅ ImgBB"
        if "telegra.ph"  in canvas_url: return "✅ Telegraph"
        if "catbox.moe"  in canvas_url: return "✅ Catbox"
        return "✅"
    return "⚠️ Upload failed — carte envoyée en aperçu Telegram"


# ── Amazon URL handler ────────────────────────────────────────
@bot.message_handler(
    func=lambda m: m.text and scraper.is_amazon_url(m.text))
def handle_amazon_url(message):
    # Extract URL from message
    words = message.text.split()
    url = next((w for w in words if scraper.is_amazon_url(w)), message.text)
    bot.send_message(message.chat.id, "⏳  Detected Amazon link — starting pipeline…")
    pipeline(url, message.chat.id)


@bot.message_handler(commands=["pincheck"])
def cmd_pincheck(message):
    """Validate Sheets rows row-by-row BEFORE generating the Pinterest CSV."""
    parts     = message.text.split()
    all_rows  = len(parts) > 1 and parts[1].lower() == "all"
    only_pend = not all_rows
    label     = "pending uniquement" if only_pend else "toutes les lignes"
    bot.reply_to(message, f"🔍 Vérification des lignes Pinterest ({label})…")
    pinterest_csv_exporter.send_pincheck_report(
        bot, message.chat.id, sheets_handler, only_pending=only_pend
    )


@bot.message_handler(commands=["pinfix"])
def cmd_pinfix(message):
    """Correct empty/invalid Pinterest board names in the Sheet (preview or apply)."""
    parts    = message.text.split()
    do_apply = len(parts) > 1 and parts[1].lower() == "apply"
    if do_apply:
        bot.reply_to(message, "🛠 Application des corrections de board dans le Sheet…")
    else:
        bot.reply_to(message, "🔎 Analyse des noms de board (aperçu, aucune écriture)…")
    pinterest_csv_exporter.send_board_fix_report(
        bot, message.chat.id, sheets_handler, apply=do_apply
    )


@bot.message_handler(commands=["startpinterest"])
def cmd_startpinterest(message):
    from scheduler import start_pinterest_scheduler, is_pinterest_running
    if is_pinterest_running():
        bot.reply_to(message, "ℹ️  Pinterest scheduler already running.")
        return
    start_pinterest_scheduler()
    import os
    interval = os.environ.get("PINTEREST_INTERVAL_MINUTES", "240")
    bot.reply_to(message,
        f"🎯  <b>Pinterest scheduler started!</b>\n"
        f"⏱  Every <b>{interval} min</b>\n"
        f"📌  Publishes pending pins from Google Sheets"
    )


@bot.message_handler(commands=["stoppinterest"])
def cmd_stoppinterest(message):
    from scheduler import stop_pinterest_scheduler, is_pinterest_running
    if not is_pinterest_running():
        bot.reply_to(message, "ℹ️  Pinterest scheduler is not running.")
        return
    stop_pinterest_scheduler()
    bot.reply_to(message, "🛑  Pinterest scheduler stopped.")


@bot.message_handler(commands=["pintest"])
def cmd_pintest(message):
    """Manually trigger one Pinterest publish cycle."""
    bot.reply_to(message, "🎯  Running Pinterest publish cycle…")
    try:
        from pinterest_publisher import publish_pending_sync
        import threading
        def run():
            publish_pending_sync(max_pins=1)
            bot.send_message(message.chat.id,
                "✅  Pinterest cycle complete. Check Sheets for status.")
        threading.Thread(target=run, daemon=True).start()
    except ImportError:
        bot.reply_to(message,
            "❌  Playwright not installed.\n"
            "Run: <code>playwright install chromium</code>")
    except Exception as e:
        bot.reply_to(message, f"❌  Error: <code>{e}</code>")


@bot.message_handler(commands=["pinstatus"])
def cmd_pinstatus(message):
    """Show Pinterest scheduler status + pending pin count."""
    from scheduler import is_pinterest_running
    running = is_pinterest_running()
    try:
        sheet   = sheets_handler.get_sheet()
        records = sheets_handler._safe_get_all_records(sheet)
        pending = sum(1 for r in records
                      if str(r.get("pinterest_status","")).lower()
                      in ("pending",""))
        published = sum(1 for r in records
                        if str(r.get("pinterest_status","")).lower() == "published")
        failed = sum(1 for r in records
                     if str(r.get("pinterest_status","")).lower() == "failed")
        sheets_info = (
            f"📊  Sheets:\n"
            f"   ⏳ Pending   : <b>{pending}</b>\n"
            f"   ✅ Published : <b>{published}</b>\n"
            f"   ❌ Failed    : <b>{failed}</b>"
        )
    except Exception as e:
        sheets_info = f"⚠️  Sheets error: <code>{e}</code>"

    import os
    interval = os.environ.get("PINTEREST_INTERVAL_MINUTES", "240")
    bot.reply_to(message,
        f"📌  <b>Pinterest Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢' if running else '🔴'}  Scheduler: <b>{'ON' if running else 'OFF'}</b>\n"
        f"⏱  Interval: <b>{interval} min</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{sheets_info}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ════════════════════════════════════════════════════════════════
# DEBUG COMMAND — temporary
@bot.message_handler(commands=["wp_debug"])
def cmd_wp_debug(message):
    import os
    results = []
    for var in ["WP_URL","WP_USERNAME","WP_APP_PASSWORD","WP_BLOG_ID"]:
        val = os.environ.get(var, "")
        icon = "✅" if val else "❌"
        preview = (val[:8] + "...") if val else "VIDE"
        results.append(f"{icon} <code>{var}</code> = <code>{preview}</code>")
    results.append("")
    results.append(f"WP_URL in module: <code>{wordpress_publisher.WP_URL or 'VIDE'}</code>")
    wp_pass_status = "SET" if wordpress_publisher.WP_PASSWORD else "VIDE"
    results.append(f"WP_PASSWORD: <code>{wp_pass_status}</code>")
    configured = wordpress_publisher.is_configured()
    results.append(f"is_configured: <code>{configured}</code>")
    sep = "\n"
    bot.reply_to(message, sep.join(results), parse_mode="HTML")


# ══════════════════════════════════════════════════════════════════
# NEW COMMANDS — Test Mode, Platform Status, Pinterest API, Sheet Fix
# ══════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["testmode"])
def cmd_testmode(message):
    global _test_mode
    parts = message.text.strip().split()
    arg   = parts[1].lower() if len(parts) > 1 else ""
    if arg == "on":
        _test_mode = True
        bot.reply_to(message,
            "🧪 <b>Mode Test ACTIVÉ</b>\n\n"
            "Le pipeline s'exécute <b>sans</b> publier sur :\n"
            "• Canal Telegram\n• WordPress\n• Blogger\n• Google Sheets\n• Pinterest\n\n"
            "Utilise /deletetest pour nettoyer les données de test.")
    elif arg == "off":
        _test_mode = False
        bot.reply_to(message, "✅ <b>Mode Test désactivé</b> — publications réelles réactivées.")
    else:
        status = "🧪 ACTIF" if _test_mode else "✅ INACTIF"
        bot.reply_to(message,
            f"Mode Test : <b>{status}</b>\n"
            "/testmode on — activer\n/testmode off — désactiver")


@bot.message_handler(commands=["deletetest"])
def cmd_deletetest(message):
    global _test_results
    n = len(_test_results)
    _test_results.clear()
    bot.reply_to(message,
        f"🗑 <b>Données de test supprimées</b>\n{n} entrée(s) effacée(s) de la mémoire.")


@bot.message_handler(commands=["platformstatus"])
def cmd_platformstatus(message):
    """Platform status — check all configured services."""
    bot.send_chat_action(message.chat.id, "typing")
    try:
        import json as _j
        from core.safe_redis import safe_get

        # Total articles from AI feedback
        total_articles = 0
        try:
            raw = safe_get("feedback:stats:global")
            if raw:
                total_articles = _j.loads(raw).get("total", 0)
        except Exception:
            pass

        # ── AMAZON platforms ──────────────────────────────────────────────────
        try:
            wp_ok = "✅ Connected" if wordpress_publisher.is_configured() else "⚠️ Not configured"
        except Exception:
            wp_ok = "⚠️ Not configured"

        bl_ok  = "✅ Active" if os.environ.get("BLOGGER_BLOG_ID") else "⚠️ Missing"
        tg_ok  = "✅ Active" if os.environ.get("CHANNEL_ID") else "⚠️ Missing CHANNEL_ID"
        pin_ok = "✅ Active" if os.environ.get("PINTEREST_ACCESS_TOKEN") else "⚠️ Missing token"

        # Queue
        q_count = 0
        try:
            q_count = queue_manager.pending_count()
        except Exception:
            pass

        dash    = ""

        msg = (
            f"📊 <b>Platform Status</b>\n"
            f"{'━'*30}\n\n"

            f"🛒 <b>Platforms</b>\n"
            f"  📢 Telegram:  {tg_ok}\n"
            f"  📝 WordPress: {wp_ok}\n"
            f"  📰 Blogger:   {bl_ok}\n"
            f"  📌 Pinterest: {pin_ok}\n\n"

            f"{'━'*30}\n"
            f"📦 Queue:          <b>{q_count}</b>\n"
            f"📈 Total articles: <b>{total_articles}</b>\n"
        )
        if dash:
            msg += f"\n🔗 <a href=\'{dash}\'>Dashboard</a>"

        bot.reply_to(message, msg, parse_mode="HTML")

    except Exception as e:
        bot.reply_to(message, f"❌ Error: <code>{e}</code>", parse_mode="HTML")

@bot.message_handler(commands=["pinapi"])
def cmd_pinapi(message):
    if not _pinterest_api_available:
        bot.reply_to(message, "⚠️ pinterest_api.py non déployé. Utilise /pinterestcsv à la place.")
        return
    bot.send_chat_action(message.chat.id, "typing")
    ok, msg = pinterest_api.test_connection()
    cap  = int(os.environ.get("PINTEREST_DAILY_CAP", "5"))
    hrs  = float(os.environ.get("PINTEREST_HOURS_AHEAD", "4"))
    icon = "✅" if ok else "❌"
    bot.reply_to(message,
        f"📌 <b>Pinterest API v5</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Statut   : {icon} {msg}\n"
        f"Plafond  : <b>{cap} pins/jour</b>\n"
        f"Décalage : <b>+{hrs}h</b> (publication différée)\n"
        f"Intervalle auto : <b>toutes les {int(24*60/cap)} min</b>\n\n"
        f"{'✅ Scheduler actif' if scheduler.is_pinterest_running() else '⏸ Scheduler inactif'}\n\n"
        f"📌 Setup : obtenez votre token sur\n"
        f"https://developers.pinterest.com/tools/oauth-token-generator/\n"
        f"puis ajoutez PINTEREST_ACCESS_TOKEN dans .env"
    )


@bot.message_handler(commands=["pinpublish"])
def cmd_pinpublish(message):
    if not _pinterest_api_available:
        bot.reply_to(message, "⚠️ pinterest_api.py non déployé. Utilise /pinterestcsv à la place.")
        return
    if not pinterest_api.is_configured():
        bot.reply_to(message,
            "❌ Pinterest API non configurée.\n"
            "Ajoutez <code>PINTEREST_ACCESS_TOKEN</code> dans .env.\n"
            "Token : https://developers.pinterest.com/tools/oauth-token-generator/")
        return
    parts = message.text.split()
    n     = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
    bot.send_chat_action(message.chat.id, "typing")
    bot.reply_to(message, f"📌 Publication de jusqu'à <b>{n}</b> pins via API v5…")

    def _notify(msg):
        try:
            bot.send_message(message.chat.id, msg)
        except Exception:
            pass

    result = pinterest_api.publish_pending_api(max_pins=n, notify=_notify)
    bot.send_message(message.chat.id,
        f"📌 <b>Publication Pinterest terminée</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ Publiés  : {result['published']}\n"
        f"❌ Échoués  : {result['failed']}\n"
        f"⏭ Ignorés  : {result.get('skipped',0)}\n"
        + (f"\n⚠️ Erreurs :\n" + "\n".join(result['errors'][:5]) if result['errors'] else "")
    )


@bot.message_handler(commands=["pinaudit"])
def cmd_pinaudit(message):
    """Scan Google Sheets and report all Pinterest-related errors."""
    if not _sheets_repair_available:
        bot.reply_to(message, "❌ sheets_repair.py non déployé.")
        return
    bot.send_chat_action(message.chat.id, "typing")
    bot.reply_to(message, "🔍 Analyse du Sheet en cours…")
    try:
        records = sheets_handler._safe_get_all_records(sheets_handler.get_sheet())
        report  = sheets_repair.analyze_csv(b"", records)   # Sheet-only scan

        cdn_n     = len(report["amazon_cdn"])
        sim_n     = len(report["similar_titles"])
        board_n   = len(report["wrong_board"])
        link_n    = len(report["missing_link"])

        lines = [
            f"🔍 <b>Audit Pinterest — {report['sheet_total']} lignes</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🖼 Images Amazon CDN    : <b>{cdn_n}</b> ← cause principale des rejets",
            f"✏️ Titres quasi-dupliqués: <b>{sim_n} paires</b>",
            f"📌 Boards à améliorer   : <b>{board_n}</b>",
            f"🔗 Liens manquants      : <b>{link_n}</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if cdn_n:
            lines.append(f"\n🖼 <b>Images à re-héberger (top 5)</b>")
            for r in report["amazon_cdn"][:5]:
                lines.append(f"  L{r['sheet_row']}: {r['title']}")

        if sim_n:
            lines.append(f"\n✏️ <b>Titres à différencier (top 5)</b>")
            for p in report["similar_titles"][:5]:
                lines.append(f"  L{p['row_a']} vs L{p['row_b']}: {p['title_a'][:35]}…")
                lines.append(f"    Variant A: {p['variant_a']}  |  B: {p['variant_b']}")

        lines += [
            f"\n💡 <b>Commandes de correction :</b>",
            f"/pinrepair media   — re-héberger les images ({cdn_n} lignes)",
            f"/pinrepair titles  — différencier les titres ({sim_n} paires)",
            f"/pinrepair boards  — corriger les boards ({board_n} lignes)",
            f"/pinrepair all     — tout corriger en une commande",
        ]
        from pinterest_csv_exporter import _send_chunks
        _send_chunks(bot, message.chat.id, lines)
    except Exception as e:
        bot.reply_to(message, f"❌ Erreur: <code>{e}</code>")


@bot.message_handler(commands=["pinrepair"])
def cmd_pinrepair(message):
    """Fix Pinterest errors in Google Sheets."""
    if not _sheets_repair_available:
        bot.reply_to(message, "❌ sheets_repair.py non déployé.")
        return
    parts   = message.text.strip().split()
    action  = parts[1].lower() if len(parts) > 1 else "preview"

    ACTIONS = {
        "media":   "re-hébergement des images Amazon CDN",
        "titles":  "différenciation des titres",
        "boards":  "correction des boards",
        "all":     "réparation complète",
        "preview": "aperçu (aucune écriture)",
    }
    label = ACTIONS.get(action, action)
    bot.reply_to(message, f"🔧 <b>/pinrepair {action}</b> — {label}\n⏳ En cours…")

    msgs = []
    def _notify(m):
        msgs.append(m)
        if len(msgs) % 10 == 0:
            try:
                bot.send_message(message.chat.id, "\n".join(msgs[-10:]))
            except Exception:
                pass

    try:
        records = sheets_handler._safe_get_all_records(sheets_handler.get_sheet())

        if action == "preview":
            report = sheets_repair.analyze_csv(b"", records)
            bot.send_message(message.chat.id,
                f"📋 <b>Aperçu réparation</b>\n"
                f"🖼 Images CDN     : {len(report['amazon_cdn'])}\n"
                f"✏️ Titres dupliqués: {len(report['similar_titles'])} paires\n"
                f"📌 Boards          : {len(report['wrong_board'])}\n\n"
                f"Utilise /pinrepair all pour tout corriger."
            )

        elif action == "media":
            r = sheets_repair.repair_media_urls(records, notify=_notify, dry_run=False)
            bot.send_message(message.chat.id,
                f"🖼 <b>Images re-hébergées</b>\n"
                f"✅ Corrigées : {r['fixed']}\n"
                f"❌ Échouées  : {r['failed']}\n"
                f"Total traité : {r['total']}"
            )

        elif action == "titles":
            changes = sheets_repair.repair_similar_titles(records, notify=_notify)
            bot.send_message(message.chat.id,
                f"✏️ <b>Titres différenciés : {len(changes)}</b>\n"
                + "\n".join(changes[:15])
            )

        elif action == "boards":
            r = sheets_repair.repair_boards(records, notify=_notify)
            bot.send_message(message.chat.id,
                f"📌 <b>Boards corrigés : {r['fixed']}</b>"
            )

        elif action == "all":
            result = sheets_repair.run_full_repair(notify=_notify, dry_run=False)
            bot.send_message(message.chat.id,
                f"🎉 <b>Réparation complète terminée</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 Boards    : {result['boards'].get('fixed',0)} corrigés\n"
                f"✏️ Titres    : {result['titles_fixed']} différenciés\n"
                f"🖼 Images   : {result['media'].get('fixed',0)} re-hébergées\n"
                f"               {result['media'].get('failed',0)} échouées\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"💡 Lance /pinterestcsv pour générer le nouveau CSV ✅"
            )

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Erreur /pinrepair: <code>{e}</code>")


@bot.message_handler(commands=["pinmarkdone"])
def cmd_pinmarkdone(message):
    """
    Apply green background + strikethrough to all already-published Pinterest rows.
    Useful for rows marked before the visual formatting feature was added.
    """
    bot.send_chat_action(message.chat.id, "typing")
    bot.reply_to(message, "🎨 Application du formatage vert/barré sur les lignes publiées…")
    try:
        result = sheets_handler.mark_all_published_rows()
        bot.send_message(message.chat.id,
            f"✅ <b>Formatage appliqué</b>\n"
            f"🟢 Lignes formatées (publiées) : <b>{result.get('formatted',0)}</b>\n"
            f"⬜ Ignorées (pas encore publiées) : {result.get('skipped',0)}\n\n"
            f"Les lignes publiées apparaissent maintenant en vert barré dans Google Sheets."
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Erreur: <code>{e}</code>")



# ── Catch-all fallback — MUST be the LAST message handler registered ──────────
# telebot runs the first handler that matches and stops. A func=lambda m: True
# handler matches every message, so anything registered AFTER it is shadowed.
# Keeping it last lets all command handlers above run normally.
@bot.message_handler(func=lambda m: True)
def fallback(message):
    # Ignorer les messages du canal (ne jamais répondre au canal)
    import config as _cfg
    if str(message.chat.id) == str(_cfg.CHANNEL_ID or "").replace("@",""):
        return
    if message.chat.type in ("channel","supergroup") and str(message.chat.id) == str(_cfg.CHANNEL_ID or ""):
        return
    bot.reply_to(message,
        "💡 Envoie un lien Amazon pour commencer.\n"
        "Ou tape /help pour voir toutes les commandes."
    )

# ════════════════════════════════════════════════════════════════
# FLASK WEBHOOK SERVER
# Telegram sends updates to our webhook URL instead of polling.
# ════════════════════════════════════════════════════════════════

from flask import Flask, request, abort
import telebot as _telebot_module

flask_app = Flask(__name__)

WEBHOOK_SECRET  = os.environ.get("WEBHOOK_SECRET", "secret123changeme")
PUBLIC_DOMAIN   = os.environ.get("PUBLIC_DOMAIN", "medmml-amazon-bot-pin.hf.space")
PORT            = int(os.environ.get("PORT", "7860"))


@flask_app.get("/")
def root():
    """Root — health check endpoint."""
    return {"status": "ok", "bot": "Amazon Affiliate Bot v4"}, 200


@flask_app.get("/health")
def health():
    """
    Health check — compatible with standard health check paths.
    """
    return {
        "status":  "ok",
        "service": "Amazon Affiliate Bot v4",
        "webhook": bool(WEBHOOK_SECRET),
        "channel": bool(config.CHANNEL_ID),
    }, 200


import queue as _queue

# Single-worker update queue.
# The webhook ACKs Telegram instantly by enqueuing the update, then ONE
# long-lived worker processes updates sequentially. This:
#   - never blocks the webhook (no Telegram timeout/retry storms),
#   - never spawns unbounded threads or runs concurrent heavy pipelines
#     (which would OOM a small host),
#   - logs any handler exception so failures are visible in the logs.
_update_queue: "_queue.Queue" = _queue.Queue()


def _update_worker():
    logger.info("[webhook] update worker started")
    while True:
        update = _update_queue.get()
        try:
            bot.process_new_updates([update])
        except Exception as e:
            logger.error(f"[webhook] update processing error: {e}", exc_info=True)
        finally:
            _update_queue.task_done()


import threading as _threading
_threading.Thread(target=_update_worker, daemon=True, name="tg-update-worker").start()




# ════════════════════════════════════════════════════════════════
# WEB DASHBOARD — /dashboard
# ════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr" dir="ltr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoAffiliate Pro — Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0a0a0f;
    color: #e0e0e0;
    min-height: 100vh;
    padding: 24px;
  }}
  .header {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 32px;
  }}
  .header h1 {{
    font-size: 1.6rem;
    background: linear-gradient(135deg, #4ade80, #3b82f6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .badge {{
    background: #1a3a2a;
    color: #4ade80;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.75rem;
    border: 1px solid #4ade8030;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .card {{
    background: #111118;
    border: 1px solid #ffffff10;
    border-radius: 16px;
    padding: 20px;
    transition: border-color 0.2s;
  }}
  .card:hover {{ border-color: #4ade8040; }}
  .card .label {{
    font-size: 0.8rem;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 8px;
  }}
  .card .value {{
    font-size: 2.2rem;
    font-weight: 700;
    color: #4ade80;
    line-height: 1;
  }}
  .card .sub {{
    font-size: 0.75rem;
    color: #555;
    margin-top: 6px;
  }}
  .chart-card {{
    background: #111118;
    border: 1px solid #ffffff10;
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 24px;
  }}
  .chart-card h2 {{
    font-size: 1rem;
    color: #aaa;
    margin-bottom: 16px;
  }}
  .services {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
  }}
  .service {{
    background: #111118;
    border: 1px solid #ffffff10;
    border-radius: 12px;
    padding: 14px 16px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 0.85rem;
  }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
  .dot.green {{ background: #4ade80; box-shadow: 0 0 6px #4ade80; }}
  .dot.yellow {{ background: #fbbf24; }}
  .dot.red {{ background: #f87171; }}
  .articles-table {{
    background: #111118;
    border: 1px solid #ffffff10;
    border-radius: 16px;
    overflow: hidden;
  }}
  .articles-table h2 {{
    padding: 20px 24px 16px;
    font-size: 1rem;
    color: #aaa;
    border-bottom: 1px solid #ffffff08;
  }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{
    padding: 12px 24px;
    text-align: left;
    font-size: 0.75rem;
    color: #555;
    text-transform: uppercase;
    background: #0d0d14;
  }}
  td {{
    padding: 14px 24px;
    font-size: 0.85rem;
    border-top: 1px solid #ffffff06;
  }}
  .score {{ font-weight: 700; }}
  .score.good {{ color: #4ade80; }}
  .score.ok {{ color: #fbbf24; }}
  .score.bad {{ color: #f87171; }}
  .platform-badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 0.7rem;
    margin-right: 4px;
    background: #1e3a5f;
    color: #93c5fd;
  }}
  .updated {{
    text-align: center;
    color: #333;
    font-size: 0.75rem;
    padding: 16px;
  }}
</style>
</head>
<body>

<div class="header">
  <div>🤖</div>
  <h1>AutoAffiliate Pro</h1>
  <span class="badge">● Live</span>
</div>

<!-- Stats Cards -->
<div class="grid">
  <div class="card">
    <div class="label">Articles aujourd'hui</div>
    <div class="value">{today_articles}</div>
    <div class="sub">objectif: 5/jour</div>
  </div>
  <div class="card">
    <div class="label">Pinterest pins</div>
    <div class="value">{today_pins}</div>
    <div class="sub">cap: {pin_cap}/jour</div>
  </div>
  <div class="card">
    <div class="label">Total articles</div>
    <div class="value">{total_articles}</div>
    <div class="sub">depuis le début</div>
  </div>
  <div class="card">
    <div class="label">Meilleur score IA</div>
    <div class="value">{best_score}</div>
    <div class="sub">/ 100</div>
  </div>
  <div class="card">
    <div class="label">Queue pending</div>
    <div class="value">{queue_count}</div>
    <div class="sub">en attente</div>
  </div>
  <div class="card">
    <div class="label">Provider IA actif</div>
    <div class="value" style="font-size:1.1rem;padding-top:6px">{top_provider}</div>
    <div class="sub">meilleur score</div>
  </div>
</div>

<!-- Chart -->
<div class="chart-card">
  <h2>📈 Articles publiés — 7 derniers jours</h2>
  <canvas id="weekChart" height="80"></canvas>
</div>

<!-- Services -->
<div class="chart-card">
  <h2>🏥 État des services</h2>
  <div class="services">
    <div class="service">
      <div class="dot {redis_dot}"></div>Redis
    </div>
    <div class="service">
      <div class="dot {pg_dot}"></div>PostgreSQL
    </div>
    <div class="service">
      <div class="dot {groq_dot}"></div>Groq AI
    </div>
    <div class="service">
      <div class="dot {gemini_dot}"></div>Gemini AI
    </div>
    <div class="service">
      <div class="dot {wp_dot}"></div>WordPress
    </div>
    <div class="service">
      <div class="dot {blogger_dot}"></div>Blogger
    </div>
    <div class="service">
      <div class="dot {pin_dot}"></div>Pinterest
    </div>
    <div class="service">
      <div class="dot {sentry_dot}"></div>Sentry
    </div>
  </div>
</div>

<!-- Recent Articles -->
<div class="articles-table">
  <h2>📰 Derniers articles publiés</h2>
  <table>
    <thead>
      <tr>
        <th>Titre</th>
        <th>Score</th>
        <th>Plateformes</th>
        <th>Date</th>
      </tr>
    </thead>
    <tbody>
      {articles_rows}
    </tbody>
  </table>
</div>

<div class="updated">Actualisé le {updated_at} — <a href="/dashboard" style="color:#333">↻ Rafraîchir</a></div>

<script>
new Chart(document.getElementById("weekChart"), {{
  type: "line",
  data: {{
    labels: {week_labels},
    datasets: [{{
      label: "Articles",
      data: {week_data},
      borderColor: "#4ade80",
      backgroundColor: "#4ade8015",
      fill: true,
      tension: 0.4,
      pointRadius: 4,
      pointBackgroundColor: "#4ade80",
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: "#ffffff08" }}, ticks: {{ color: "#555" }} }},
      y: {{ grid: {{ color: "#ffffff08" }}, ticks: {{ color: "#555" }}, beginAtZero: true }}
    }}
  }}
}});
</script>
</body>
</html>"""


def _get_dashboard_data() -> dict:
    """جمع بيانات Dashboard من Redis وQueue."""
    from datetime import datetime, timedelta

    # ── إحصائيات الـ AI Feedback ─────────────────────────────────────────
    today_articles = 0
    total_articles = 0
    best_score     = 0
    top_provider   = "Groq"
    week_data      = [0] * 7

    try:
        from core.safe_redis import safe_get
        import json as _json

        # Global stats
        stats_raw = safe_get("feedback:stats:global")
        if stats_raw:
            stats        = _json.loads(stats_raw)
            total_articles = stats.get("total", 0)

        # Provider feedback
        for provider in ["groq", "gemini", "openrouter", "cloudflare"]:
            scores_raw = safe_get(f"feedback:scores:{provider}")
            if scores_raw:
                scores_list = _json.loads(scores_raw)
                if scores_list:
                    scores    = [s["score"] for s in scores_list]
                    max_score = max(scores)
                    if max_score > best_score:
                        best_score   = max_score
                        top_provider = provider.title()

        # Today count (Pinterest)
        today_key   = f"dedup:pins:{datetime.now().strftime('%Y-%m-%d')}"
        today_count = safe_get(today_key)
        today_pins  = int(today_count) if today_count else 0

    except Exception:
        pass

    # ── Week labels ────────────────────────────────────────────────────────
    week_labels = []
    for i in range(6, -1, -1):
        d = datetime.now() - timedelta(days=i)
        week_labels.append(d.strftime("%a %d"))

    # ── Services status ────────────────────────────────────────────────────
    def _dot(ok): return "green" if ok else "yellow"

    from core.safe_redis import get_safe_redis
    redis_ok  = get_safe_redis() is not None
    pg_ok     = bool(os.environ.get("DATABASE_URL"))
    groq_ok   = bool(os.environ.get("GROQ_API_KEY"))
    gemini_ok = bool(os.environ.get("GEMINI_API_KEY"))
    pin_ok    = bool(os.environ.get("PINTEREST_ACCESS_TOKEN"))
    sentry_ok = bool(os.environ.get("SENTRY_DSN"))

    try:
        wp_ok = wordpress_publisher.test_connection()
    except Exception:
        wp_ok = False
    blogger_ok = bool(os.environ.get("BLOGGER_BLOG_ID"))

    return {
        "today_articles": today_articles,
        "today_pins":     today_pins,
        "total_articles": total_articles,
        "best_score":     best_score or "—",
        "queue_count":    queue_manager.pending_count(),
        "top_provider":   top_provider,
        "week_labels":    str(week_labels),
        "week_data":      str(week_data),
        "pin_cap":        os.environ.get("PINTEREST_DAILY_CAP", "5"),
        "updated_at":     datetime.now().strftime("%d/%m/%Y %H:%M"),
        "redis_dot":      _dot(redis_ok),
        "pg_dot":         _dot(pg_ok),
        "groq_dot":       _dot(groq_ok),
        "gemini_dot":     _dot(gemini_ok),
        "wp_dot":         _dot(wp_ok),
        "blogger_dot":    _dot(blogger_ok),
        "pin_dot":        _dot(pin_ok),
        "sentry_dot":     _dot(sentry_ok),
        "articles_rows":  _get_articles_rows(),
    }


def _get_articles_rows() -> str:
    """آخر 8 مقالات من Redis."""
    try:
        from core.safe_redis import safe_lrange
        import json as _json

        rows  = []
        items = safe_lrange("dashboard:recent_articles", 0, 7)
        for raw in items:
            try:
                a     = _json.loads(raw)
                score = a.get("score", 0)
                css   = "good" if score >= 75 else ("ok" if score >= 55 else "bad")
                title = a.get("title", "—")[:55]
                plat  = " ".join(
                    f'<span class="platform-badge">{p}</span>'
                    for p in a.get("platforms", ["WP"])
                )
                date  = a.get("date", "—")[:10]
                rows.append(
                    f"<tr><td>{title}</td>"
                    f'<td><span class="score {css}">{score}/100</span></td>'
                    f"<td>{plat}</td><td>{date}</td></tr>"
                )
            except Exception:
                pass

        if not rows:
            rows = ["<tr><td colspan=\"4\" style=\"text-align:center;color:#444;padding:32px\">"
                    "Aucun article enregistré pour l'instant</td></tr>"]
        return "\n".join(rows)
    except Exception:
        return "<tr><td colspan=\"4\">—</td></tr>"


@flask_app.get("/dashboard")
def web_dashboard():
    """Dashboard web accessible sur /dashboard."""
    try:
        data = _get_dashboard_data()
        html = DASHBOARD_HTML.format(**data)
        from flask import Response
        return Response(html, mimetype="text/html")
    except Exception as e:
        return f"<h2>Dashboard error: {e}</h2>", 500



@flask_app.post(f"/webhook/{WEBHOOK_SECRET}")
def telegram_webhook():
    if request.content_type != "application/json":
        abort(403)
    update = _telebot_module.types.Update.de_json(request.get_json())
    _update_queue.put(update)          # ACK Telegram immediately
    return "ok", 200


def _start_services():
    logger.info("🚀 Bot v4 HYBRID — Amazon→Canal→WP→Blogger→Canvas→Sheets→Pinterest (API)")
    logger.info(f"   Channel   : {config.CHANNEL_ID or 'NOT SET'}")
    logger.info(f"   WordPress : {wordpress_publisher.WP_URL or 'NOT SET'}")
    logger.info(f"   Blogger   : {'configured ✅' if blogger_api_publisher.is_configured() else 'NOT SET ❌'}")
    logger.info(f"   Tag       : {config.AFFILIATE_TAG}")
    logger.info(f"   Test Mode : {'🧪 ACTIVE' if _test_mode else 'off'}")
    # WhatsApp: supprimé (utiliser Telegram uniquement)

    # Pinterest API v5 auto-scheduler (lightweight — no Playwright, no OOM)
    if _pinterest_api_available and pinterest_api.is_configured():
        try:
            scheduler.start_pinterest_scheduler()
            cap = int(os.environ.get("PINTEREST_DAILY_CAP", "5"))
            hrs = float(os.environ.get("PINTEREST_HOURS_AHEAD", "4"))
            logger.info(f"📌 Pinterest API scheduler started — {cap} pins/day, +{hrs}h ahead")
        except Exception as e:
            logger.warning(f"Pinterest API scheduler failed: {e}")
    else:
        logger.info("📌 Pinterest: API mode disponible — configurer PINTEREST_ACCESS_TOKEN")

    # Weekly CSV: supprimé — utiliser /dashboard

    # Playwright Pinterest auto-post (heavy) — OPT-IN only
    _autopost = os.environ.get("ENABLE_PINTEREST_AUTOPOST", "0").strip().lower() in ("1", "true", "yes")
    if _autopost:
        try:
            from scheduler import start_pinterest_scheduler as _sps
            # Only start if API scheduler not already running
            if not scheduler.is_pinterest_running():
                _sps()
                logger.info("🎯 Pinterest Playwright scheduler started (ENABLE_PINTEREST_AUTOPOST=1)")
        except Exception as e:
            logger.warning(f"Playwright Pinterest scheduler not started: {e}")

    # ── Uptime Robot ping (كل 4 دقائق) ──────────────────────────────────────────
    def _ping_uptime():
        import requests as _req
        ping_url = os.environ.get("UPTIME_PING_URL", "")
        if ping_url:
            try:
                _req.get(ping_url, timeout=5)
                logger.debug("[uptime] ping sent")
            except Exception:
                pass
        threading.Timer(240, _ping_uptime).start()

    _ping_uptime()
    logger.info("✅ Uptime ping started (every 4 min)")

    # ── Backup تلقائي يومي الساعة 3 صباحاً ───────────────────────────────────
    def _auto_backup():
        import json, io
        try:
            admin_id = os.environ.get("ADMIN_CHAT_ID", "")
            if not admin_id:
                return
            backup = {
                "timestamp":   datetime.now().isoformat(),
                "auto_backup": True,
                "queue_count": queue_manager.pending_count(),
                "services": {
                    "wordpress": bool(os.environ.get("WP_SITE_URL")),
                    "blogger":   bool(os.environ.get("BLOGGER_BLOG_ID")),
                    "pinterest": bool(os.environ.get("PINTEREST_ACCESS_TOKEN")),
                    "groq":      bool(os.environ.get("GROQ_API_KEY")),
                },
            }
            content_b = json.dumps(backup, ensure_ascii=False, indent=2).encode()
            filename  = f"auto_backup_{datetime.now().strftime('%Y%m%d')}.json"
            bot.send_document(
                int(admin_id),
                io.BytesIO(content_b),
                visible_file_name=filename,
                caption=f"📦 Backup automatique — {datetime.now().strftime('%d/%m/%Y')}",
            )
            logger.info("[backup] Backup automatique envoyé")
        except Exception as e:
            logger.warning(f"[backup] Auto backup failed: {e}")

    def _schedule_backup():
        now      = datetime.now()
        target   = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= target:
            from datetime import timedelta
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        threading.Timer(delay, _run_daily_backup).start()
        logger.info(f"[backup] Prochain backup dans {delay/3600:.1f}h")

    def _run_daily_backup():
        _auto_backup()
        _schedule_backup()  # planifier le suivant

    _schedule_backup()


    # ── تقرير يومي تلقائي الساعة 8 مساءً ──────────────────────────────────
    def _send_daily_report():
        try:
            admin_id = os.environ.get("ADMIN_CHAT_ID", "")
            if not admin_id:
                return
            from core.safe_redis import safe_get
            import json as _json
            total = 0
            try:
                raw   = safe_get("feedback:stats:global")
                total = _json.loads(raw).get("total", 0) if raw else 0
            except Exception:
                pass
            app_url = os.environ.get("PUBLIC_DOMAIN", "")
            dashboard_link = f"https://{app_url}/dashboard" if app_url else ""
            msg = (
                f"📊 <b>Rapport quotidien</b>\n"
                f"{'━'*26}\n"
                f"🤖 Bot: <b>En ligne ✅</b>\n"
                f"📝 File d'attente: <b>{queue_manager.pending_count()}</b>\n"
                f"📈 Total articles: <b>{total}</b>\n"
                f"{'━'*26}\n"
            )
            if dashboard_link:
                msg += f"🔗 <a href=\'{dashboard_link}\'>Voir Dashboard</a>"
            bot.send_message(int(admin_id), msg, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[daily_report] {e}")

    def _schedule_daily_report():
        from datetime import datetime, timedelta
        now    = datetime.now()
        target = now.replace(hour=20, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        def _run():
            _send_daily_report()
            _schedule_daily_report()
        threading.Timer(delay, _run).start()
        logger.info(f"[daily_report] Prochain rapport dans {delay/3600:.1f}h")

    _schedule_daily_report()


    # ── Daily Scheduler — اكتشاف تلقائي يومي ────────────────────────────────
    try:
        from daily_scheduler import start as _start_daily
        admin_id = os.environ.get("ADMIN_CHAT_ID", "")
        if admin_id:
            started = _start_daily(bot, int(admin_id))
            if started:
                hour = os.environ.get("AUTO_DISCOVER_HOUR", "8")
                n    = os.environ.get("AUTO_ARTICLES_PER_DAY", "5")
                auto = os.environ.get("AUTO_PUBLISH_MODE", "false")
                logger.info(
                    f"🤖 Daily scheduler: {hour}h00 | "
                    f"{n} articles/jour | auto={auto}"
                )
            else:
                logger.info(
                    "🤖 Daily scheduler: désactivé "
                    "(AUTO_DISCOVER_ENABLED=true pour activer)"
                )
        else:
            logger.info("🤖 Daily scheduler: ADMIN_CHAT_ID manquant")
    except Exception as _e:
        logger.warning(f"Daily scheduler non démarré: {_e}")

    # Register Telegram webhook or start polling
    if PUBLIC_DOMAIN:
        webhook_url = f"https://{PUBLIC_DOMAIN}/webhook/{WEBHOOK_SECRET}"
        try:
            bot.remove_webhook()
            import time; time.sleep(1)
            bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            logger.info(f"✅ Webhook registered: {webhook_url}")
        except Exception as e:
            logger.error(f"❌ Webhook registration failed: {e}")
    else:
        logger.info("🔄 PUBLIC_DOMAIN not set — starting polling mode")
        try:
            bot.remove_webhook()
            import time; time.sleep(1)
            t = threading.Thread(target=bot.infinity_polling, kwargs={"timeout": 60, "long_polling_timeout": 60}, daemon=True)
            t.start()
            logger.info("✅ Polling started")
        except Exception as e:
            logger.error(f"❌ Polling failed: {e}")


# ── تشغيل الخدمات — يعمل مع gunicorn وبشكل مستقل ──────────────────────────
# Gunicorn لا يشغّل if __name__ == "__main__"
# لذا نستدعي _start_services() على مستوى الـ module
_start_services()

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True)
