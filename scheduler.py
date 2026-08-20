"""
scheduler.py — Thread-based schedulers: Telegram auto-post + Pinterest API auto-publish.
Pinterest publishing uses lightweight API v5 (no Playwright, no Chromium).
Interval: PINTEREST_DAILY_CAP pins per day, spread PINTEREST_HOURS_AHEAD hours from now.

Phase 0: sheets_handler + pinterest_csv_exporter supprimés (modules deleted).
"""
import os, time, logging, threading
from datetime import datetime

import config, queue_manager

# sheets_handler supprimé — import optionnel pour compatibilité
try:
    import sheets_handler as _sh
    _sheets_available = True
except ImportError:
    _sh = None
    _sheets_available = False
    logging.getLogger(__name__).info(
        "[scheduler] sheets_handler non trouvé — mode queue uniquement"
    )

logger = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
_scheduler_thread: threading.Thread | None = None
_pinterest_thread: threading.Thread | None = None
_stop_event       = threading.Event()
_pinterest_stop   = threading.Event()
_interval_minutes = config.DEFAULT_INTERVAL_MINUTES

def _pinterest_interval() -> int:
    cap = max(1, int(os.environ.get("PINTEREST_DAILY_CAP", "5")))
    return max(60, int(24 * 60 / cap))

_post_callback = None


# ── Telegram scheduler ────────────────────────────────────────────────────────

def set_post_callback(fn):
    global _post_callback
    _post_callback = fn

def set_interval(minutes: int):
    global _interval_minutes
    _interval_minutes = max(1, minutes)
    logger.info(f"[scheduler] Interval → {_interval_minutes} min")

def get_interval() -> int:
    return _interval_minutes

def is_running() -> bool:
    return _scheduler_thread is not None and _scheduler_thread.is_alive()

def start():
    global _scheduler_thread, _stop_event
    if is_running():
        return False
    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_loop, daemon=True, name="tg-scheduler"
    )
    _scheduler_thread.start()
    logger.info(f"[scheduler] Telegram started ({_interval_minutes} min)")
    return True

def stop():
    global _scheduler_thread
    _stop_event.set()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=5)
        _scheduler_thread = None
    logger.info("[scheduler] Telegram stopped")

def _loop():
    while not _stop_event.is_set():
        try:
            _tick()
        except Exception as e:
            logger.error(f"[scheduler] tick: {e}")
        for _ in range(_interval_minutes * 12):
            if _stop_event.is_set():
                break
            time.sleep(5)

def _tick():
    """
    Prend le prochain item depuis queue_manager.
    Si vide et sheets_handler disponible → tente depuis Sheets (optionnel).
    """
    if _post_callback is None:
        return

    item = queue_manager.pop_next()

    # Fallback Sheets uniquement si disponible
    if item is None and _sheets_available and _sh:
        try:
            pending = _sh.get_pending_products()
            if pending:
                row  = pending[0]
                item = {
                    "title":       row.get("Title", ""),
                    "media_url":   row.get("Media URL", ""),
                    "description": row.get("Description", ""),
                    "aff_link":    row.get("Link", "") or row.get("Affiliate Link", ""),
                    "price":       row.get("Price", "N/A"),
                    "img_url":     row.get("Amazon Image", ""),
                    "asin":        row.get("ASIN", ""),
                    "keywords":    row.get("Keywords", ""),
                    "board":       row.get("Pinterest Board", ""),
                }
        except Exception as e:
            logger.warning(f"[scheduler] sheets fallback failed: {e}")

    if item is None:
        return

    try:
        _post_callback(item)
        asin = item.get("asin", "")
        if asin:
            queue_manager.mark_posted(asin)
            # Mark in Sheets uniquement si disponible
            if _sheets_available and _sh:
                try:
                    _sh.mark_posted(asin)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"[scheduler] post failed: {e}")


# ── Pinterest API scheduler (lightweight — no Playwright) ─────────────────────

def start_pinterest_scheduler():
    global _pinterest_thread, _pinterest_stop
    if _pinterest_thread and _pinterest_thread.is_alive():
        return False
    _pinterest_stop.clear()
    _pinterest_thread = threading.Thread(
        target=_pinterest_loop, daemon=True, name="pin-api-scheduler"
    )
    _pinterest_thread.start()
    interval = _pinterest_interval()
    logger.info(f"[scheduler] Pinterest API scheduler started (every {interval} min)")
    return True

def stop_pinterest_scheduler():
    global _pinterest_thread
    _pinterest_stop.set()
    if _pinterest_thread:
        _pinterest_thread.join(timeout=10)
        _pinterest_thread = None
    logger.info("[scheduler] Pinterest scheduler stopped")

def is_pinterest_running() -> bool:
    return _pinterest_thread is not None and _pinterest_thread.is_alive()

def _pinterest_loop():
    logger.info("[scheduler] Pinterest API loop started")
    time.sleep(15)
    while not _pinterest_stop.is_set():
        try:
            _run_pinterest_api()
        except Exception as e:
            logger.error(f"[scheduler] Pinterest loop error: {e}")
        interval = _pinterest_interval()
        for _ in range(interval * 12):
            if _pinterest_stop.is_set():
                break
            time.sleep(5)
    logger.info("[scheduler] Pinterest API loop exited")

def _run_pinterest_api():
    try:
        import pinterest_api as pa
        if not pa.is_configured():
            logger.info("[scheduler] Pinterest API token not set — skipping")
            return
        logger.info("[scheduler] Pinterest: publishing pending pins via API…")
        result = pa.publish_pending_api(max_pins=1)
        if result["published"]:
            logger.info(f"[scheduler] Pinterest: {result['published']} pin(s) published ✅")
        if result["errors"]:
            for e in result["errors"]:
                logger.warning(f"[scheduler] Pinterest: {e}")
    except ImportError:
        logger.warning("[scheduler] pinterest_api.py not found")
    except Exception as e:
        logger.error(f"[scheduler] Pinterest API error: {e}")


# ── Weekly CSV — désactivé (pinterest_csv_exporter supprimé) ──────────────────

def start_weekly_csv(bot, admin_chat_id):
    """
    Fonction conservée pour compatibilité — CSV supprimé.
    Utiliser /dashboard pour les statistiques.
    """
    logger.info("[scheduler] Weekly CSV désactivé — utiliser /dashboard")
    return False
