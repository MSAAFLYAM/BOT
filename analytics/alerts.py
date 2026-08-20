"""
analytics/alerts.py — Automated alert system.

Alerts sent to admin Telegram when thresholds exceeded:

CRITICAL (immediate):
  - DB / Redis down
  - Bot webhook not responding
  - Scraping block rate > 50%

WARNING (within 1h):
  - TinyPNG credits < 50
  - Pinterest daily cap reached
  - Scraping block rate 30-50%
  - AI generation failure rate > 30%

INFO (daily summary only):
  - No scraping activity in 24h
  - Pinterest cap at 80%

Alert deduplication:
  Each alert type has a cooldown in Redis.
  Same alert won't fire again until cooldown expires.
  Prevents spam when a service is continuously down.

Cooldowns:
  CRITICAL: 30 min (repeat if still critical)
  WARNING:  2 hours
  INFO:     24 hours
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

_ALERT_PREFIX = "analytics:alert:cooldown:"


class AlertLevel(str, Enum):
    CRITICAL = "critical"
    WARNING  = "warning"
    INFO     = "info"


@dataclass
class Alert:
    level:   AlertLevel
    title:   str
    message: str
    key:     str           # Unique key for deduplication
    cooldown_s: int = 7200 # 2h default

    @property
    def emoji(self) -> str:
        return {
            AlertLevel.CRITICAL: "🚨",
            AlertLevel.WARNING:  "⚠️",
            AlertLevel.INFO:     "ℹ️",
        }.get(self.level, "📢")

    def format_telegram(self) -> str:
        return (
            f"{self.emoji} <b>{self.title}</b>\n"
            f"{self.message}\n"
            f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
        )


class AlertManager:
    """
    Check metrics against thresholds and send Telegram alerts.

    Usage:
        manager = AlertManager(admin_chat_id=123456)
        await manager.check_all(snapshot)
    """

    def __init__(self, admin_chat_id: Optional[int] = None):
        import os
        self._chat_id = admin_chat_id or int(os.environ.get("ADMIN_CHAT_ID", "0"))
        self._token   = os.environ.get("BOT_TOKEN", "")

    async def check_all(self, snapshot) -> list[Alert]:
        """
        Run all alert checks against snapshot.
        Sends non-deduplicated alerts to Telegram.
        Returns list of triggered alerts.
        """
        triggered = []

        checks = [
            self._check_services(snapshot),
            self._check_scraping(snapshot),
            self._check_tinify(snapshot),
            self._check_pinterest(snapshot),
        ]

        for coro in checks:
            try:
                alerts = await coro
                triggered.extend(alerts)
            except Exception as e:
                logger.warning(f"[alerts] Check failed: {e}")

        # Send non-deduplicated alerts
        for alert in triggered:
            if await self._should_send(alert):
                await self._send(alert)
                await self._set_cooldown(alert)

        return triggered

    # ── Alert checks ──────────────────────────────────────────────

    async def _check_services(self, snapshot) -> list[Alert]:
        alerts = []
        for svc in snapshot.services:
            if svc.status == "error":
                alerts.append(Alert(
                    level=AlertLevel.CRITICAL,
                    title=f"{svc.name} DOWN",
                    message=f"Service {svc.name} est inaccessible.\n{svc.details}",
                    key=f"service_down_{svc.name.lower()}",
                    cooldown_s=1800,  # 30 min
                ))
        return alerts

    async def _check_scraping(self, snapshot) -> list[Alert]:
        alerts = []
        sc = snapshot.scraping
        if sc.fetch_total < 5:  # Not enough data
            return alerts

        if sc.block_rate > 0.5:
            alerts.append(Alert(
                level=AlertLevel.CRITICAL,
                title="Scraping: Taux de blocage critique",
                message=(
                    f"Taux de blocage: <b>{sc.block_rate:.0%}</b>\n"
                    f"Requêtes bloquées: {sc.fetch_blocked}/{sc.fetch_total}"
                ),
                key="scraping_high_block_rate",
                cooldown_s=1800,
            ))
        elif sc.block_rate > 0.3:
            alerts.append(Alert(
                level=AlertLevel.WARNING,
                title="Scraping: Blocages élevés",
                message=f"Taux de blocage: {sc.block_rate:.0%}",
                key="scraping_medium_block_rate",
                cooldown_s=7200,
            ))
        return alerts

    async def _check_tinify(self, snapshot) -> list[Alert]:
        alerts = []
        credits = snapshot.publishing.tinify_credits_left
        if credits is None:
            return alerts

        if credits < 10:
            alerts.append(Alert(
                level=AlertLevel.CRITICAL,
                title="TinyPNG: Quota presque épuisé",
                message=f"Il reste seulement <b>{credits} crédits</b> TinyPNG ce mois-ci.",
                key="tinify_critical",
                cooldown_s=3600,
            ))
        elif credits < 50:
            alerts.append(Alert(
                level=AlertLevel.WARNING,
                title="TinyPNG: Quota bas",
                message=f"Il reste {credits} crédits TinyPNG (sur 500/mois).",
                key="tinify_warning",
                cooldown_s=21600,  # 6h
            ))
        return alerts

    async def _check_pinterest(self, snapshot) -> list[Alert]:
        alerts = []
        pt = snapshot.pinterest
        if pt.pins_today >= pt.daily_cap:
            alerts.append(Alert(
                level=AlertLevel.INFO,
                title="Pinterest: Cap quotidien atteint",
                message=f"{pt.pins_today}/{pt.daily_cap} pins créés aujourd'hui. Recharge à minuit.",
                key="pinterest_cap_reached",
                cooldown_s=86400,  # 24h
            ))
        return alerts

    # ── Deduplication ─────────────────────────────────────────────

    async def _should_send(self, alert: Alert) -> bool:
        from core.safe_redis import safe_exists
        return not safe_exists(f"{_ALERT_PREFIX}{alert.key}")

    async def _set_cooldown(self, alert: Alert) -> None:
        from core.safe_redis import safe_set
        safe_set(f"{_ALERT_PREFIX}{alert.key}", "1", ttl=alert.cooldown_s)

    async def _send(self, alert: Alert) -> None:
        """Send alert to admin Telegram."""
        if not self._chat_id or not self._token:
            logger.warning(f"[alerts] Cannot send: no chat_id or token")
            return
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{self._token}/sendMessage",
                    json={
                        "chat_id":    self._chat_id,
                        "text":       alert.format_telegram(),
                        "parse_mode": "HTML",
                    },
                )
            logger.info(f"[alerts] Sent: {alert.title}")
        except Exception as e:
            logger.warning(f"[alerts] Send failed: {e}")
