"""
analytics/tasks.py — Celery tasks + Beat schedule for analytics.

Scheduled tasks (Celery Beat):
  Every 5 min  → collect_metrics (update Redis snapshot)
  Every 5 min  → check_alerts    (send Telegram alerts if thresholds exceeded)
  Daily 09:00  → send_daily_report
  Hourly       → check_tinify_credits
  Weekly Mon   → send_weekly_report

One-time tasks (triggered manually or by other tasks):
  send_dashboard  → send dashboard to specific chat
  run_health_check→ check all services

Beat schedule uses UTC times.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _run(coro):
    """Run async coroutine from sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def register_tasks(celery_app):
    """Register all analytics Celery tasks."""

    @celery_app.task(name="analytics.collect_metrics", queue="default")
    def collect_metrics_task() -> dict:
        """Collect all metrics and cache in Redis."""
        async def _collect():
            from analytics.collector import get_collector
            snap = await get_collector().collect()
            return snap.to_dict()
        try:
            result = _run(_collect())
            logger.info(f"[analytics] Metrics collected: {result.get('timestamp','')}")
            return {"status": "ok", "timestamp": result.get("timestamp")}
        except Exception as e:
            logger.error(f"[analytics] Collect failed: {e}")
            return {"status": "error", "error": str(e)[:100]}

    @celery_app.task(name="analytics.check_alerts", queue="default")
    def check_alerts_task(admin_chat_id: Optional[int] = None) -> dict:
        """Check all alert thresholds and send Telegram alerts."""
        async def _check():
            from analytics.collector import get_collector
            from analytics.alerts import AlertManager
            snap    = await get_collector().get_cached() or await get_collector().collect()
            chat_id = admin_chat_id or int(os.environ.get("ADMIN_CHAT_ID", "0"))
            manager = AlertManager(chat_id)
            alerts  = await manager.check_all(snap)
            return {
                "status":          "ok",
                "alerts_triggered": len(alerts),
                "alerts":          [{"level": a.level.value, "title": a.title}
                                   for a in alerts],
            }
        try:
            return _run(_check())
        except Exception as e:
            return {"status": "error", "error": str(e)[:100]}

    @celery_app.task(name="analytics.send_daily_report", queue="default")
    def send_daily_report_task(admin_chat_id: Optional[int] = None) -> dict:
        """Send daily analytics report to admin."""
        async def _send():
            chat_id = admin_chat_id or int(os.environ.get("ADMIN_CHAT_ID", "0"))
            from analytics.dashboard import send_daily_report
            success = await send_daily_report(chat_id)
            return {"status": "ok" if success else "failed", "chat_id": chat_id}
        try:
            return _run(_send())
        except Exception as e:
            return {"status": "error", "error": str(e)[:100]}

    @celery_app.task(name="analytics.send_dashboard", queue="default")
    def send_dashboard_task(
        chat_id: int,
        compact: bool = False,
    ) -> dict:
        """Send dashboard to specific Telegram chat."""
        async def _send():
            from analytics.dashboard import send_dashboard
            success = await send_dashboard(chat_id, compact=compact, use_cache=False)
            return {"status": "ok" if success else "failed"}
        try:
            return _run(_send())
        except Exception as e:
            return {"status": "error", "error": str(e)[:100]}

    @celery_app.task(name="analytics.health_check_all", queue="default")
    def health_check_all_task() -> dict:
        """Run full health check across all services."""
        async def _check():
            from analytics.collector import MetricsCollector
            collector = MetricsCollector()
            services  = await collector._collect_services()
            statuses  = {s.name: s.status for s in services}
            all_ok    = all(s.status in ("ok","unconfigured") for s in services)
            return {
                "status":   "ok" if all_ok else "degraded",
                "services": statuses,
            }
        try:
            return _run(_check())
        except Exception as e:
            return {"status": "error", "error": str(e)[:100]}

    return {
        "collect_metrics":    collect_metrics_task,
        "check_alerts":       check_alerts_task,
        "send_daily_report":  send_daily_report_task,
        "send_dashboard":     send_dashboard_task,
        "health_check_all":   health_check_all_task,
    }


def get_beat_schedule() -> dict:
    """
    Return Celery Beat schedule for analytics tasks.

    Add to celery_app.conf.beat_schedule in scheduler/celery_app.py:
        from analytics.tasks import get_beat_schedule
        app.conf.beat_schedule.update(get_beat_schedule())
    """
    from celery.schedules import crontab

    return {
        # Every 5 minutes: collect metrics
        "analytics-collect-metrics": {
            "task":     "analytics.collect_metrics",
            "schedule": 300,  # 5 minutes
        },

        # Every 5 minutes: check alerts
        "analytics-check-alerts": {
            "task":     "analytics.check_alerts",
            "schedule": 300,
            "kwargs":   {"admin_chat_id": int(os.environ.get("ADMIN_CHAT_ID", "0"))},
        },

        # Every day at 09:00 UTC: daily report
        "analytics-daily-report": {
            "task":     "analytics.send_daily_report",
            "schedule": crontab(hour=9, minute=0),
            "kwargs":   {"admin_chat_id": int(os.environ.get("ADMIN_CHAT_ID", "0"))},
        },

        # Every hour: health check
        "analytics-health-check": {
            "task":     "analytics.health_check_all",
            "schedule": 3600,
        },

        # Pinterest daily stats (check cap)
        "pinterest-daily-stats": {
            "task":     "pinterest.daily_stats",
            "schedule": crontab(hour=20, minute=0),  # 8 PM UTC
        },

        # Check TinyPNG credits daily
        "tinify-check-credits": {
            "task":     "publishing.check_tinify",
            "schedule": crontab(hour=8, minute=0),
        },
    }
