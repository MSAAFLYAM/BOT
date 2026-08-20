"""
analytics/dashboard.py — Telegram dashboard message builder.
analytics/reports.py   — Daily/weekly automated reports.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════

class DashboardBuilder:
    """
    Build formatted Telegram dashboard messages.

    Called from:
      - /dashboard command in main.py
      - Analytics tasks (periodic refresh)

    Format: HTML with emojis, sections per phase.
    """

    def build_full(self, snapshot) -> str:
        """Build complete dashboard message."""
        now  = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        lines= [
            f"📊 <b>DASHBOARD — {now}</b>",
            f"",
        ]

        # Service health
        lines.append("🔧 <b>Services</b>")
        for svc in snapshot.services:
            lat = f" ({svc.latency_ms:.0f}ms)" if svc.latency_ms else ""
            lines.append(f"  {svc.emoji} {svc.name}{lat}")
        lines.append("")

        # Scraping
        sc = snapshot.scraping
        lines.append(f"🕷️ <b>Scraping</b> {sc.emoji}")
        if sc.fetch_total > 0:
            lines.append(f"  • Requêtes: {sc.fetch_total} (✅{sc.fetch_success} ❌{sc.fetch_blocked})")
            lines.append(f"  • Succès: {sc.success_rate:.0%} | Blocage: {sc.block_rate:.0%}")
            lines.append(f"  • Cache hits: {sc.cache_hits}")
            if sc.browser_fallbacks > 0:
                lines.append(f"  • Browser fallbacks: {sc.browser_fallbacks}")
        else:
            lines.append("  • Aucune activité")
        lines.append("")

        # DB content
        db = snapshot.db
        lines.append("🗄️ <b>Base de données</b>")
        lines.append(f"  • Produits: {db.products_total:,}")
        lines.append("")

        # Publishing
        pub = snapshot.publishing
        lines.append("📢 <b>Publication</b>")
        if pub.total_published > 0:
            lines.append(f"  • WordPress: {pub.wordpress_published}")
            lines.append(f"  • Blogger:   {pub.blogger_published}")
            lines.append(f"  • Telegram:  {pub.telegram_published}")
            lines.append(f"  • WhatsApp:  {pub.whatsapp_published}")
        else:
            lines.append("  • Aucune publication")

        # TinyPNG
        if pub.tinify_credits_left is not None:
            pct  = int(pub.tinify_credits_left / 5)   # 500 credits = 100%
            bar  = "█" * (pct // 10) + "░" * (10 - pct // 10)
            emoji= "🔴" if pub.tinify_credits_left < 50 else "🟡" if pub.tinify_credits_left < 100 else "🟢"
            lines.append(f"  • TinyPNG {emoji}: [{bar}] {pub.tinify_credits_left}/500")
        lines.append("")

        # AI
        ai = snapshot.ai
        lines.append("🤖 <b>IA</b>")
        lines.append(f"  • Articles générés: {ai.articles_generated}")
        if ai.avg_score > 0:
            lines.append(f"  • Score moyen: {ai.avg_score:.0f}/100")
        lines.append("")

        # Pinterest
        pt = snapshot.pinterest
        lines.append("📌 <b>Pinterest</b>")
        bar_filled = int(pt.pins_today / max(1, pt.daily_cap) * 10)
        bar  = "█" * bar_filled + "░" * (10 - bar_filled)
        emoji= "🔴" if pt.pins_today >= pt.daily_cap else "🟢"
        lines.append(f"  • Aujourd'hui {emoji}: [{bar}] {pt.pins_today}/{pt.daily_cap}")
        lines.append("")

        lines.append(f"<i>Collecté en {snapshot.uptime_s*1000:.0f}ms</i>")

        return "\n".join(lines)

    def build_compact(self, snapshot) -> str:
        """Build compact one-line-per-service dashboard."""
        sc  = snapshot.scraping
        pub = snapshot.publishing
        pt  = snapshot.pinterest
        db  = snapshot.db

        services_ok = sum(1 for s in snapshot.services if s.status == "ok")
        services_total = len(snapshot.services)

        lines = [
            f"📊 <b>Status rapide</b> — {datetime.now(timezone.utc).strftime('%H:%M')}",
            f"🔧 Services: {services_ok}/{services_total} OK",
            f"🕷️ Scraping: {sc.fetch_success}/{sc.fetch_total} ✅ | Blocage: {sc.block_rate:.0%}",
            f"🗄️ DB: {db.products_total:,} produits",
            f"📢 Publiés: {pub.total_published} total",
            f"📌 Pinterest: {pt.pins_today}/{pt.daily_cap} aujourd'hui",
        ]
        if pub.tinify_credits_left is not None:
            lines.append(f"🖼️ TinyPNG: {pub.tinify_credits_left}/500 crédits")

        return "\n".join(lines)

    def build_inline_keyboard(self) -> dict:
        """Build Telegram inline keyboard for dashboard."""
        return {
            "inline_keyboard": [[
                {"text": "🔄 Actualiser",    "callback_data": "dashboard:refresh"},
                {"text": "🕷️ Scraping",      "callback_data": "dashboard:scraping"},
            ], [
                {"text": "📢 Publications",  "callback_data": "dashboard:publishing"},
                {"text": "📌 Pinterest",     "callback_data": "dashboard:pinterest"},
            ], [
                {"text": "⚠️ Alertes",       "callback_data": "dashboard:alerts"},
                {"text": "📈 Rapport",       "callback_data": "dashboard:report"},
            ]]
        }


async def send_dashboard(
    chat_id:   int,
    compact:   bool = False,
    use_cache: bool = True,
) -> bool:
    """
    Collect metrics and send dashboard to Telegram chat.

    Args:
        chat_id:   Telegram chat ID
        compact:   Send compact version (default: full)
        use_cache: Use cached snapshot if available

    Returns:
        True if sent successfully.
    """
    import os, httpx
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        return False

    try:
        from analytics.collector import get_collector
        collector = get_collector()

        if use_cache:
            snapshot = await collector.get_cached() or await collector.collect()
        else:
            snapshot = await collector.collect()

        builder = DashboardBuilder()
        text    = builder.build_compact(snapshot) if compact else builder.build_full(snapshot)
        keyboard= builder.build_inline_keyboard()

        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id":      chat_id,
                    "text":         text,
                    "parse_mode":   "HTML",
                    "reply_markup": keyboard,
                },
            )
        return True

    except Exception as e:
        logger.error(f"[dashboard] Send failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════
# REPORTS
# ══════════════════════════════════════════════════════════════════

class ReportBuilder:
    """
    Build daily/weekly analytics reports.

    Daily report sent every day at 9:00 AM.
    Weekly report sent every Monday at 9:00 AM.
    """

    def build_daily(self, snapshot, date_str: str = "") -> str:
        """Build daily report message."""
        if not date_str:
            from datetime import timedelta
            yesterday = (datetime.now(timezone.utc) -
                        timedelta(days=1)).strftime("%d/%m/%Y")
            date_str  = yesterday

        sc  = snapshot.scraping
        pub = snapshot.publishing
        pt  = snapshot.pinterest
        db  = snapshot.db
        ai  = snapshot.ai

        services_ok    = sum(1 for s in snapshot.services if s.status == "ok")
        services_total = len(snapshot.services)

        lines = [
            f"📈 <b>RAPPORT QUOTIDIEN — {date_str}</b>",
            f"",
            f"🏆 <b>Résumé</b>",
            f"  • Services actifs: {services_ok}/{services_total}",
            f"  • Produits en DB: {db.products_total:,}",
            f"",
            f"🕷️ <b>Scraping</b>",
            f"  • Requêtes: {sc.fetch_total}",
            f"  • Taux de succès: {sc.success_rate:.0%}",
            f"  • Taux de blocage: {sc.block_rate:.0%}",
            f"  • Cache hits: {sc.cache_hits}",
            f"",
            f"🤖 <b>IA</b>",
            f"  • Articles générés: {ai.articles_generated}",
            f"",
            f"📢 <b>Publications totales</b>",
            f"  • WordPress: {pub.wordpress_published}",
            f"  • Blogger:   {pub.blogger_published}",
            f"  • Telegram:  {pub.telegram_published}",
            f"  • WhatsApp:  {pub.whatsapp_published}",
            f"",
            f"📌 <b>Pinterest</b>",
            f"  • Pins hier: {pt.pins_today}/{pt.daily_cap}",
            f"",
        ]

        if pub.tinify_credits_left is not None:
            lines.append(f"🖼️ <b>TinyPNG</b>: {pub.tinify_credits_left}/500 crédits restants")

        # Status finale
        issues = [s for s in snapshot.services if s.status == "error"]
        if issues:
            lines.append(f"")
            lines.append(f"⚠️ <b>Problèmes détectés</b>")
            for s in issues:
                lines.append(f"  ❌ {s.name}: {s.details or 'indisponible'}")
        else:
            lines.append(f"")
            lines.append(f"✅ <b>Tous les services fonctionnent normalement</b>")

        return "\n".join(lines)


async def send_daily_report(admin_chat_id: int) -> bool:
    """Collect metrics and send daily report."""
    import os, httpx
    token = os.environ.get("BOT_TOKEN", "")
    if not token or not admin_chat_id:
        return False

    try:
        from analytics.collector import get_collector
        snapshot = await get_collector().collect()
        text     = ReportBuilder().build_daily(snapshot)

        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id":    admin_chat_id,
                    "text":       text,
                    "parse_mode": "HTML",
                },
            )
        logger.info(f"[reports] Daily report sent to {admin_chat_id}")
        return True

    except Exception as e:
        logger.error(f"[reports] Failed: {e}")
        return False
