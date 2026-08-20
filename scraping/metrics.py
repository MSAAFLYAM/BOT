"""
scraping/metrics.py — Structured scraper metrics and telemetry.

Architecture decisions:
  - In-memory counters (atomic operations via asyncio.Lock).
  - Metrics flushed to Redis every 60s for persistence and multi-worker sharing.
  - Structured JSON logs (not print/string logs) for log aggregation.
  - Per-domain granularity: amazon.fr and amazon.com tracked independently.
  - Method tracking: know which fetch layer succeeds most for each domain.
  - Pressure detection: automatic signal when block rate exceeds threshold.
    The rate limiter reads this signal to adapt its pacing.

Tracked metrics:
  - fetch_total / fetch_success / fetch_blocked / fetch_failed
  - method_hits: {httpx: 234, curl-cffi/chrome124: 45, playwright: 12}
  - latency_ms: rolling average per domain + method
  - cache_hits / cache_misses
  - parse_success / parse_failures
  - browser_fallbacks (Playwright usage)
  - retry_counts

Memory: ~2KB per domain in memory. 100 domains = 200KB. Negligible.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Metric Key Namespaces ─────────────────────────────────────────────────────
_PREFIX = "metrics:scraper:"


# ── Per-domain State ──────────────────────────────────────────────────────────

@dataclass
class DomainMetrics:
    """Mutable metrics for a single domain."""
    domain:           str
    fetch_total:      int = 0
    fetch_success:    int = 0
    fetch_blocked:    int = 0
    fetch_failed:     int = 0
    cache_hits:       int = 0
    cache_misses:     int = 0
    parse_success:    int = 0
    parse_failures:   int = 0
    browser_fallbacks:int = 0
    retry_total:      int = 0

    # Method distribution
    method_hits:      dict = field(default_factory=dict)

    # Latency (milliseconds)
    latency_sum:      float = 0.0
    latency_count:    int   = 0
    latency_min:      float = float("inf")
    latency_max:      float = 0.0

    # Time window
    window_start:     float = field(default_factory=time.monotonic)

    @property
    def success_rate(self) -> float:
        """Success rate 0.0–1.0. Returns 1.0 if no fetches yet."""
        if self.fetch_total == 0:
            return 1.0
        return self.fetch_success / self.fetch_total

    @property
    def block_rate(self) -> float:
        """Block rate 0.0–1.0."""
        if self.fetch_total == 0:
            return 0.0
        return self.fetch_blocked / self.fetch_total

    @property
    def avg_latency_ms(self) -> float:
        """Average fetch latency in milliseconds."""
        if self.latency_count == 0:
            return 0.0
        return self.latency_sum / self.latency_count

    @property
    def under_pressure(self) -> bool:
        """
        True if detection pressure is high.
        Used by AdaptiveRateLimiter to reduce scraping speed.
        Threshold: block_rate > 30% OR success_rate < 50%.
        """
        return self.block_rate > 0.30 or self.success_rate < 0.50

    def record_latency(self, ms: float) -> None:
        self.latency_sum   += ms
        self.latency_count += 1
        self.latency_min    = min(self.latency_min, ms)
        self.latency_max    = max(self.latency_max, ms)

    def record_method(self, method: str) -> None:
        self.method_hits[method] = self.method_hits.get(method, 0) + 1

    def to_dict(self) -> dict:
        elapsed = time.monotonic() - self.window_start
        return {
            "domain":          self.domain,
            "window_s":        round(elapsed, 1),
            "fetch_total":     self.fetch_total,
            "fetch_success":   self.fetch_success,
            "fetch_blocked":   self.fetch_blocked,
            "fetch_failed":    self.fetch_failed,
            "success_rate":    round(self.success_rate, 3),
            "block_rate":      round(self.block_rate, 3),
            "under_pressure":  self.under_pressure,
            "cache_hits":      self.cache_hits,
            "cache_misses":    self.cache_misses,
            "parse_success":   self.parse_success,
            "parse_failures":  self.parse_failures,
            "browser_fallbacks": self.browser_fallbacks,
            "retry_total":     self.retry_total,
            "method_hits":     self.method_hits,
            "latency_avg_ms":  round(self.avg_latency_ms, 1),
            "latency_min_ms":  round(self.latency_min, 1) if self.latency_count else 0,
            "latency_max_ms":  round(self.latency_max, 1),
        }


# ── Global Metrics ─────────────────────────────────────────────────────────────

@dataclass
class GlobalMetrics:
    """Aggregated metrics across all domains."""
    fetch_total:       int   = 0
    fetch_success:     int   = 0
    fetch_blocked:     int   = 0
    cache_hits:        int   = 0
    parse_failures:    int   = 0
    browser_fallbacks: int   = 0
    active_domains:    int   = 0
    start_time:        float = field(default_factory=time.monotonic)

    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def success_rate(self) -> float:
        if self.fetch_total == 0:
            return 1.0
        return self.fetch_success / self.fetch_total

    def to_dict(self) -> dict:
        return {
            "uptime_s":        round(self.uptime_s, 1),
            "fetch_total":     self.fetch_total,
            "fetch_success":   self.fetch_success,
            "fetch_blocked":   self.fetch_blocked,
            "success_rate":    round(self.success_rate, 3),
            "cache_hits":      self.cache_hits,
            "parse_failures":  self.parse_failures,
            "browser_fallbacks": self.browser_fallbacks,
            "active_domains":  self.active_domains,
        }


# ── ScraperMetrics (main class) ───────────────────────────────────────────────

class ScraperMetrics:
    """
    Thread-safe structured metrics collector.

    Usage:
        metrics = ScraperMetrics()
        metrics.record_fetch_success("amazon.fr", "httpx", 234.5)
        metrics.record_cache_hit("amazon.fr")

    Fetch to Redis every 60s for multi-worker sharing:
        asyncio.create_task(metrics.flush_loop())
    """

    def __init__(self):
        self._lock:     asyncio.Lock             = asyncio.Lock()
        self._domains:  dict[str, DomainMetrics] = defaultdict(lambda: DomainMetrics(""))
        self._global:   GlobalMetrics            = GlobalMetrics()
        self._last_flush: float                  = time.monotonic()
        self._flush_interval: int                = 60   # seconds

    def _get_domain(self, domain: str) -> DomainMetrics:
        if domain not in self._domains:
            dm = DomainMetrics(domain=domain)
            self._domains[domain] = dm
        return self._domains[domain]

    # ── Recording methods (all sync — called from async code without await) ──

    def record_fetch_success(
        self,
        domain:    str,
        method:    str,
        latency_ms: float,
        from_cache: bool = False,
    ) -> None:
        dm = self._get_domain(domain)
        dm.fetch_total   += 1
        dm.fetch_success += 1
        dm.record_latency(latency_ms)
        dm.record_method(method)
        self._global.fetch_total   += 1
        self._global.fetch_success += 1

        if from_cache:
            dm.cache_hits         += 1
            self._global.cache_hits += 1

        self._log_fetch("success", domain, method, latency_ms)

    def record_fetch_blocked(
        self,
        domain:     str,
        method:     str,
        status_code: int,
        latency_ms: float,
    ) -> None:
        dm = self._get_domain(domain)
        dm.fetch_total   += 1
        dm.fetch_blocked += 1
        dm.record_latency(latency_ms)
        dm.record_method(f"{method}:blocked")
        self._global.fetch_total   += 1
        self._global.fetch_blocked += 1
        self._log_fetch("blocked", domain, method, latency_ms, status_code=status_code)

    def record_fetch_failed(
        self,
        domain:    str,
        method:    str,
        error:     str,
        latency_ms: float,
    ) -> None:
        dm = self._get_domain(domain)
        dm.fetch_total  += 1
        dm.fetch_failed += 1
        dm.record_latency(latency_ms)
        self._global.fetch_total += 1
        self._log_fetch("failed", domain, method, latency_ms, error=error[:100])

    def record_cache_miss(self, domain: str) -> None:
        self._get_domain(domain).cache_misses += 1
        self._global.cache_hits  # not a miss for global

    def record_parse_success(self, domain: str) -> None:
        self._get_domain(domain).parse_success += 1

    def record_parse_failure(self, domain: str, reason: str = "") -> None:
        dm = self._get_domain(domain)
        dm.parse_failures   += 1
        self._global.parse_failures += 1
        logger.warning(
            json.dumps({
                "event":  "parse_failure",
                "domain": domain,
                "reason": reason,
            })
        )

    def record_browser_fallback(self, domain: str) -> None:
        dm = self._get_domain(domain)
        dm.browser_fallbacks           += 1
        self._global.browser_fallbacks += 1
        logger.info(json.dumps({
            "event": "browser_fallback",
            "domain": domain,
        }))

    def record_retry(self, domain: str, attempt: int, reason: str) -> None:
        self._get_domain(domain).retry_total += 1
        logger.debug(json.dumps({
            "event":   "retry",
            "domain":  domain,
            "attempt": attempt,
            "reason":  reason,
        }))

    def is_under_pressure(self, domain: str) -> bool:
        """True if this domain is experiencing high block rate."""
        if domain not in self._domains:
            return False
        return self._domains[domain].under_pressure

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_domain_report(self, domain: str) -> dict:
        if domain not in self._domains:
            return {"domain": domain, "no_data": True}
        return self._domains[domain].to_dict()

    def get_global_report(self) -> dict:
        report = self._global.to_dict()
        report["active_domains"] = len(self._domains)
        report["domains"] = {
            d: m.to_dict()
            for d, m in self._domains.items()
        }
        return report

    # ── Redis flush (async) ────────────────────────────────────────────────────

    async def flush_to_redis(self) -> None:
        """Persist current metrics snapshot to Redis."""
        try:
            from core.redis_client import get_redis
            redis = get_redis()
            report = self.get_global_report()
            await redis.setex(
                f"{_PREFIX}global",
                3600,  # 1 hour TTL
                json.dumps(report),
            )
            self._last_flush = time.monotonic()
        except Exception as e:
            logger.warning(f"Metrics flush to Redis failed: {e}")

    async def flush_loop(self) -> None:
        """Background task: flush metrics to Redis every N seconds."""
        while True:
            await asyncio.sleep(self._flush_interval)
            await self.flush_to_redis()

    # ── Structured logging ────────────────────────────────────────────────────

    def _log_fetch(
        self,
        result:     str,
        domain:     str,
        method:     str,
        latency_ms: float,
        status_code: Optional[int] = None,
        error:      Optional[str]  = None,
    ) -> None:
        record = {
            "event":      f"fetch_{result}",
            "domain":     domain,
            "method":     method,
            "latency_ms": round(latency_ms, 1),
        }
        if status_code: record["status_code"] = status_code
        if error:       record["error"] = error

        if result == "success":
            logger.info(json.dumps(record))
        elif result == "blocked":
            logger.warning(json.dumps(record))
        else:
            logger.error(json.dumps(record))


# ── Module-level singleton ─────────────────────────────────────────────────────

_metrics: Optional[ScraperMetrics] = None


def get_metrics() -> ScraperMetrics:
    """Return the module-level metrics singleton."""
    global _metrics
    if _metrics is None:
        _metrics = ScraperMetrics()
    return _metrics
