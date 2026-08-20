"""
analytics/collector.py — Real-time metrics aggregator.

Collects metrics from ALL phases:
  Phase 1: DB health, Redis health
  Phase 2: Scraping success/block rates, cache hits, browser fallbacks
  Phase 3: AI generation stats, quality scores, model usage
  Phase 4: Publishing stats per platform, TinyPNG credits
  Phase 5: API endpoint usage, webhook processing
  Phase 6: Pinterest daily pins, board stats

All metrics stored in Redis (TTL 24h).
Aggregated into MetricsSnapshot dataclass.
Used by dashboard.py and reports.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_SNAPSHOT_KEY = "analytics:snapshot:latest"
_SNAPSHOT_TTL = 3600  # 1 hour


@dataclass
class ServiceHealth:
    name:       str
    status:     str   = "unknown"   # ok / degraded / error / unconfigured
    latency_ms: float = 0.0
    details:    str   = ""

    @property
    def emoji(self) -> str:
        return {"ok": "✅", "degraded": "⚠️", "error": "❌", "unconfigured": "⚙️"}.get(self.status, "❓")


@dataclass
class ScrapingMetrics:
    fetch_total:       int   = 0
    fetch_success:     int   = 0
    fetch_blocked:     int   = 0
    cache_hits:        int   = 0
    browser_fallbacks: int   = 0
    parse_failures:    int   = 0
    success_rate:      float = 0.0
    block_rate:        float = 0.0

    @property
    def emoji(self) -> str:
        if self.block_rate > 0.3:   return "🔴"
        if self.block_rate > 0.1:   return "🟡"
        return "🟢"


@dataclass
class PublishingMetrics:
    wordpress_published: int = 0
    blogger_published:   int = 0
    telegram_published:  int = 0
    whatsapp_published:  int = 0
    total_published:     int = 0
    tinify_credits_used: int = 0
    tinify_credits_left: Optional[int] = None


@dataclass
class AIMetrics:
    articles_generated:  int   = 0
    avg_score:           float = 0.0
    avg_word_count:      int   = 0
    products_processed:  int   = 0


@dataclass
class PinterestMetrics:
    pins_today:    int = 0
    daily_cap:     int = 5
    total_pins:    int = 0
    boards_count:  int = 0

    @property
    def cap_pct(self) -> int:
        return int(self.pins_today / max(1, self.daily_cap) * 100)


@dataclass
class DBMetrics:
    products_total:   int = 0
    products_today:   int = 0
    jobs_running:     int = 0
    jobs_failed:      int = 0


@dataclass
class MetricsSnapshot:
    """Complete metrics snapshot from all phases."""
    timestamp:   str                  = ""
    uptime_s:    float                = 0.0

    # Service health
    services:    list[ServiceHealth]  = field(default_factory=list)

    # Phase metrics
    scraping:    ScrapingMetrics      = field(default_factory=ScrapingMetrics)
    publishing:  PublishingMetrics    = field(default_factory=PublishingMetrics)
    ai:          AIMetrics            = field(default_factory=AIMetrics)
    pinterest:   PinterestMetrics     = field(default_factory=PinterestMetrics)
    db:          DBMetrics            = field(default_factory=DBMetrics)

    def to_dict(self) -> dict:
        return {
            "timestamp":  self.timestamp,
            "uptime_s":   self.uptime_s,
            "services":   [vars(s) for s in self.services],
            "scraping":   vars(self.scraping),
            "publishing": vars(self.publishing),
            "ai":         vars(self.ai),
            "pinterest":  vars(self.pinterest),
            "db":         vars(self.db),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MetricsSnapshot":
        snap = cls(timestamp=d.get("timestamp",""), uptime_s=d.get("uptime_s",0))
        snap.scraping   = ScrapingMetrics(**d.get("scraping",{}))
        snap.publishing = PublishingMetrics(**d.get("publishing",{}))
        snap.ai         = AIMetrics(**d.get("ai",{}))
        snap.pinterest  = PinterestMetrics(**d.get("pinterest",{}))
        snap.db         = DBMetrics(**d.get("db",{}))
        snap.services   = [ServiceHealth(**s) for s in d.get("services",[])]
        return snap


# ── Collector ─────────────────────────────────────────────────────────────────

class MetricsCollector:
    """
    Collect metrics from all phases and return MetricsSnapshot.

    Each collector method is independent (fails gracefully).
    Total collection time: ~2-3s (parallel).
    """

    async def collect(self) -> MetricsSnapshot:
        """Collect all metrics in parallel."""
        start = time.monotonic()
        snap  = MetricsSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Run all collectors in parallel
        results = await asyncio.gather(
            self._collect_services(),
            self._collect_scraping(),
            self._collect_publishing(),
            self._collect_ai(),
            self._collect_pinterest(),
            self._collect_db(),
            return_exceptions=True,
        )

        if not isinstance(results[0], Exception): snap.services   = results[0]
        if not isinstance(results[1], Exception): snap.scraping   = results[1]
        if not isinstance(results[2], Exception): snap.publishing = results[2]
        if not isinstance(results[3], Exception): snap.ai         = results[3]
        if not isinstance(results[4], Exception): snap.pinterest  = results[4]
        if not isinstance(results[5], Exception): snap.db         = results[5]

        snap.uptime_s = round(time.monotonic() - start, 2)

        # Cache snapshot
        await self._cache_snapshot(snap)
        return snap

    async def get_cached(self) -> Optional[MetricsSnapshot]:
        from core.safe_redis import safe_get
        data = safe_get(_SNAPSHOT_KEY)
        if data:
            try:
                return MetricsSnapshot.from_dict(json.loads(data))
            except Exception:
                pass
        return None

    # ── Service health ────────────────────────────────────────────

    async def _collect_services(self) -> list[ServiceHealth]:
        services = []

        # PostgreSQL
        try:
            from core.database import check_database_health
            r   = await asyncio.wait_for(check_database_health(), timeout=3.0)
            svc = ServiceHealth(
                name="PostgreSQL",
                status=r.get("status","error"),
                latency_ms=r.get("latency_ms",0),
            )
        except Exception as e:
            svc = ServiceHealth("PostgreSQL", "error", details=str(e)[:50])
        services.append(svc)

        # Redis
        try:
            from core.redis_client import check_redis_health
            r   = await asyncio.wait_for(check_redis_health(), timeout=2.0)
            svc = ServiceHealth(
                name="Redis",
                status=r.get("status","error"),
                latency_ms=r.get("latency_ms",0),
            )
        except Exception as e:
            svc = ServiceHealth("Redis", "error", details=str(e)[:50])
        services.append(svc)

        # Telegram
        import os
        has_token = bool(os.environ.get("BOT_TOKEN",""))
        services.append(ServiceHealth(
            name="Telegram",
            status="ok" if has_token else "unconfigured",
        ))

        # TinyPNG
        try:
            from publishing.image import get_tinify_client
            credits = await asyncio.wait_for(
                get_tinify_client().get_remaining_credits(), timeout=5.0
            )
            status = "ok" if (credits or 0) > 50 else "degraded"
            services.append(ServiceHealth(
                "TinyPNG", status,
                details=f"{credits} crédits restants" if credits else "quota inconnu"
            ))
        except Exception:
            services.append(ServiceHealth("TinyPNG","unconfigured"))

        # Pinterest
        try:
            token = os.environ.get("PINTEREST_ACCESS_TOKEN","")
            services.append(ServiceHealth(
                "Pinterest",
                "ok" if token else "unconfigured",
            ))
        except Exception:
            services.append(ServiceHealth("Pinterest","unconfigured"))

        return services

    # ── Scraping metrics ──────────────────────────────────────────

    async def _collect_scraping(self) -> ScrapingMetrics:
        try:
            from scraping.metrics import get_metrics
            m      = get_metrics()
            report = m.get_global_report()
            return ScrapingMetrics(
                fetch_total=report.get("fetch_total",0),
                fetch_success=report.get("fetch_success",0),
                fetch_blocked=report.get("fetch_blocked",0),
                cache_hits=report.get("cache_hits",0),
                browser_fallbacks=report.get("browser_fallbacks",0),
                parse_failures=report.get("parse_failures",0),
                success_rate=round(report.get("success_rate",1.0),3),
                block_rate=round(report.get("fetch_blocked",0)/max(1,report.get("fetch_total",1)),3),
            )
        except Exception:
            return ScrapingMetrics()

    # ── Publishing metrics ────────────────────────────────────────

    async def _collect_publishing(self) -> PublishingMetrics:
        metrics = PublishingMetrics()
        try:
            from sqlalchemy import select, func
            from core.database import get_db
            from core.models.product import Product, PublishStatus

            async with get_db() as db:
                for field_name, attr in [
                    ("wp_status","wp_status"),
                    ("blogger_status","blogger_status"),
                    ("telegram_status","telegram_status"),
                    ("whatsapp_status","whatsapp_status"),
                ]:
                    try:
                        q = select(func.count()).where(
                            getattr(Product, attr) == PublishStatus.PUBLISHED
                        )
                        r = await db.execute(q)
                        count = r.scalar() or 0
                        if "wp_status" in attr:    metrics.wordpress_published = count
                        if "blogger_status" in attr: metrics.blogger_published  = count
                        if "telegram_status" in attr: metrics.telegram_published = count
                        if "whatsapp_status" in attr: metrics.whatsapp_published = count
                    except Exception:
                        pass

            metrics.total_published = (
                metrics.wordpress_published + metrics.blogger_published +
                metrics.telegram_published + metrics.whatsapp_published
            )
        except Exception:
            pass

        # TinyPNG credits
        try:
            from publishing.image import get_tinify_client
            metrics.tinify_credits_left = await asyncio.wait_for(
                get_tinify_client().get_remaining_credits(), timeout=5.0
            )
        except Exception:
            pass

        return metrics

    # ── AI metrics ────────────────────────────────────────────────

    async def _collect_ai(self) -> AIMetrics:
        try:
            from sqlalchemy import select, func
            from core.database import get_db
            from core.models.analytics import AnalyticsEvent, EventType

            async with get_db() as db:
                try:
                    q = select(func.count()).where(
                        AnalyticsEvent.event_type == EventType.GENERATED
                    ) if hasattr(EventType, 'GENERATED') else select(func.count()).limit(0)
                    r = await db.execute(q)
                    count = r.scalar() or 0
                    return AIMetrics(articles_generated=count)
                except Exception:
                    pass
        except Exception:
            pass
        return AIMetrics()

    # ── Pinterest metrics ─────────────────────────────────────────

    async def _collect_pinterest(self) -> PinterestMetrics:
        try:
            from pinterest.boards import DailyScheduler
            scheduler = DailyScheduler()
            stats     = await scheduler.get_stats()
            return PinterestMetrics(
                pins_today=stats.get("pins_today", 0),
                daily_cap=stats.get("daily_cap", 5),
            )
        except Exception:
            return PinterestMetrics()

    # ── DB metrics ────────────────────────────────────────────────

    async def _collect_db(self) -> DBMetrics:
        metrics = DBMetrics()
        try:
            from sqlalchemy import select, func
            from core.database import get_db
            from core.models.product import Product

            async with get_db() as db:
                try:
                    r = await db.execute(select(func.count()).select_from(Product))
                    metrics.products_total = r.scalar() or 0
                except Exception:
                    pass
        except Exception:
            pass
        return metrics

    # ── Cache ─────────────────────────────────────────────────────

    async def _cache_snapshot(self, snap: MetricsSnapshot) -> None:
        from core.safe_redis import safe_set
        safe_set(_SNAPSHOT_KEY, json.dumps(snap.to_dict(), default=str), ttl=_SNAPSHOT_TTL)


# ── Singleton ─────────────────────────────────────────────────────────────────

_collector: Optional[MetricsCollector] = None


def get_collector() -> MetricsCollector:
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector
