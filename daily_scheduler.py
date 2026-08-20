"""
scheduler/daily_scheduler.py — Autonomous Daily Content Discovery

يعمل تلقائياً كل يوم بدون أي تدخل.
يكتشف المحتوى → يولده بالـ AI → ينشره على كل المنصات.

الزبون يستيقظ ويجد محتوى جديداً كل يوم.

الإعداد في Environment Variables:
    AUTO_DISCOVER_ENABLED = true
    AUTO_PUBLISH_MODE     = true   (بدون موافقة يدوية)
    AUTO_DISCOVER_SITES   = https://marmiton.org,https://750g.com
    AUTO_DISCOVER_HOUR    = 8      (الساعة 8 صباحاً)
    AUTO_ARTICLES_PER_DAY = 5
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── مواقع الاكتشاف الافتراضية ────────────────────────────────────────────────
DEFAULT_SITES = [
    "https://www.amazon.com/bestsellers",
]

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()


def start(bot, admin_chat_id: int) -> bool:
    """
    تشغيل الـ scheduler التلقائي.
    يبدأ فوراً ويعمل كـ background thread.
    """
    global _scheduler_thread, _stop_event

    # تحقق من الإعداد
    enabled = os.environ.get("AUTO_DISCOVER_ENABLED", "false").lower()
    if enabled != "true":
        logger.info("[auto] AUTO_DISCOVER_ENABLED != true — scheduler désactivé")
        return False

    if _scheduler_thread and _scheduler_thread.is_alive():
        logger.info("[auto] Scheduler déjà en cours")
        return False

    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(bot, admin_chat_id),
        daemon=True,
        name="auto-discover",
    )
    _scheduler_thread.start()
    logger.info(f"[auto] ✅ Daily scheduler démarré → admin={admin_chat_id}")
    return True


def stop() -> None:
    """Arrêter le scheduler."""
    _stop_event.set()
    logger.info("[auto] Scheduler arrêté")


def is_running() -> bool:
    return _scheduler_thread is not None and _scheduler_thread.is_alive()


def _scheduler_loop(bot, admin_chat_id: int) -> None:
    """Loop principal — tourne toujours en arrière-plan."""
    logger.info("[auto] Loop démarrée")

    while not _stop_event.is_set():
        try:
            _wait_until_next_run()
            if not _stop_event.is_set():
                _run_daily_discovery(bot, admin_chat_id)
        except Exception as e:
            logger.error(f"[auto] Erreur loop: {e}")

        # Attendre 1h avant la prochaine vérification
        for _ in range(720):  # 720 × 5s = 1h
            if _stop_event.is_set():
                break
            time.sleep(5)


def _wait_until_next_run() -> None:
    """Attendre jusqu'à l'heure configurée."""
    target_hour = int(os.environ.get("AUTO_DISCOVER_HOUR", "8"))
    now         = datetime.now()
    target      = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)

    if now >= target:
        target += timedelta(days=1)

    delay = (target - now).total_seconds()
    logger.info(
        f"[auto] Prochain run: {target.strftime('%d/%m %H:%M')} "
        f"(dans {delay/3600:.1f}h)"
    )

    # Attendre en petits morceaux pour pouvoir arrêter
    while delay > 0 and not _stop_event.is_set():
        sleep_time = min(60, delay)
        time.sleep(sleep_time)
        delay -= sleep_time


def _run_daily_discovery(bot, admin_chat_id: int) -> None:
    """Lancer la découverte quotidienne."""
    import asyncio

    # Choisir un site aléatoire
    sites_env = os.environ.get("AUTO_DISCOVER_SITES", "")
    sites     = [s.strip() for s in sites_env.split(",") if s.strip()] \
                if sites_env else DEFAULT_SITES

    site     = random.choice(sites)
    n        = int(os.environ.get("AUTO_ARTICLES_PER_DAY", "5"))
    auto     = os.environ.get("AUTO_PUBLISH_MODE", "false").lower() == "true"

    logger.info(f"[auto] 🚀 Découverte quotidienne: {site} ({n} articles)")

    # Notifier l'admin
    try:
        bot.send_message(
            admin_chat_id,
            f"🤖 <b>Découverte automatique démarrée</b>\n"
            f"🌐 Site: <code>{site}</code>\n"
            f"📝 Objectif: <b>{n} articles</b>\n"
            f"{'🚀 Mode: Publication auto' if auto else '👤 Mode: Approbation manuelle'}",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Lancer le pipeline
    try:
        logger.info(f"[auto] Pipeline discovery for {site} ({n} items)")
        # Pipeline placeholder — implement Amazon product discovery here
        bot.send_message(
            admin_chat_id,
            f"🔍 Discovery started for <code>{site}</code>\n"
            f"📝 Target: <b>{n} products</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"[auto] Pipeline error: {e}")
        try:
            bot.send_message(
                admin_chat_id,
                f"❌ Erreur découverte auto: <code>{str(e)[:200]}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass
