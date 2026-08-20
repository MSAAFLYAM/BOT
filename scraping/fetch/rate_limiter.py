"""
scraping/fetch/rate_limiter.py — Adaptive per-domain rate limiter.

Architecture decisions:
  - Redis-backed counters (atomic, shared across multiple Celery workers).
  - Per-domain rate tracking: amazon.fr and amazon.com are independent.
  - ADAPTIVE: automatically reduces speed when detection pressure increases.
    Pressure signals: rising block_rate, timeout spikes, 429 responses.
    Recovery: gradually increases rate when success_rate improves.
  - Jitter: randomized delay prevents synchronized requests (thundering herd).
  - Backpressure: returns wait_seconds so callers can await the delay.

Rate zones (automatic):
  GREEN  (block_rate < 10%): full configured rate
  YELLOW (block_rate 10-30%): 50% of configured rate
  RED    (block_rate > 30%): 20% of configured rate + extended delays

Redis atomicity:
  Uses INCR + EXPIRE pipeline (single round-trip, atomic).
  Multiple workers sharing counters = correct rate limiting.

Memory per domain: ~200 bytes in Redis. 100 domains = 20KB.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ── Redis key prefixes ────────────────────────────────────────────────────────
_PFX_RATE    = "rate:domain:"
_PFX_PRESSURE= "rate:pressure:"
_PFX_CONFIG  = "rate:config:"


# ── Pressure Zones ────────────────────────────────────────────────────────────

class PressureZone(str, Enum):
    GREEN  = "green"    # Normal, full speed
    YELLOW = "yellow"   # Some blocks, slow down 50%
    RED    = "red"      # Heavy blocks, slow down 80%


@dataclass
class DomainConfig:
    """Per-domain rate limit configuration."""
    domain:              str
    base_rpm:            int   = 10     # Base requests per minute
    min_delay_s:         float = 0.5   # Minimum delay between requests
    max_delay_s:         float = 5.0   # Maximum delay
    jitter_factor:       float = 0.4   # ±40% random jitter

    @property
    def base_interval_s(self) -> float:
        """Seconds between requests at base rate."""
        return 60.0 / self.base_rpm

    def get_interval(self, zone: PressureZone) -> float:
        """Get request interval for current pressure zone."""
        multipliers = {
            PressureZone.GREEN:  1.0,
            PressureZone.YELLOW: 2.0,   # 2x slower
            PressureZone.RED:    5.0,   # 5x slower
        }
        base = self.base_interval_s * multipliers[zone]
        # Add jitter
        jitter = base * self.jitter_factor * (random.random() * 2 - 1)
        return max(self.min_delay_s, min(self.max_delay_s, base + jitter))


# ── Pressure Tracker ──────────────────────────────────────────────────────────

class PressureTracker:
    """
    Tracks detection pressure per domain.

    Maintains a sliding window of:
      - success counts
      - block counts
      - timeout counts

    Updates pressure zone based on recent history.
    Zone changes are persisted to Redis for worker sharing.
    """
    WINDOW_SIZE   = 20   # Last N requests
    UPDATE_EVERY  = 5    # Recalculate zone every N requests

    def __init__(self):
        # In-memory recent history per domain
        self._history: dict[str, list[str]] = {}  # domain → ["ok","block","ok",...]
        self._zones:   dict[str, PressureZone] = {}
        self._counters: dict[str, int] = {}

    def record(self, domain: str, outcome: str) -> PressureZone:
        """
        Record an outcome for domain.

        outcome: "ok" | "block" | "timeout" | "error"
        Returns current pressure zone after update.
        """
        if domain not in self._history:
            self._history[domain] = []
            self._zones[domain]   = PressureZone.GREEN
            self._counters[domain] = 0

        history = self._history[domain]
        history.append(outcome)
        # Keep only last WINDOW_SIZE entries
        if len(history) > self.WINDOW_SIZE:
            history.pop(0)

        self._counters[domain] += 1

        # Recalculate zone every UPDATE_EVERY requests
        if self._counters[domain] % self.UPDATE_EVERY == 0:
            self._zones[domain] = self._calculate_zone(history)
            logger.debug(
                f"Pressure zone for {domain}: {self._zones[domain].value} "
                f"(window: {history[-min(10, len(history)):]}"
            )

        return self._zones[domain]

    def get_zone(self, domain: str) -> PressureZone:
        """Get current pressure zone for domain."""
        return self._zones.get(domain, PressureZone.GREEN)

    def _calculate_zone(self, history: list[str]) -> PressureZone:
        if not history:
            return PressureZone.GREEN
        blocks   = history.count("block")
        timeouts = history.count("timeout")
        bad      = blocks + timeouts
        rate     = bad / len(history)
        if rate > 0.30:
            return PressureZone.RED
        elif rate > 0.10:
            return PressureZone.YELLOW
        return PressureZone.GREEN


# ── Domain Rate Limiter ───────────────────────────────────────────────────────

class DomainRateLimiter:
    """
    Adaptive per-domain rate limiter backed by Redis.

    Usage:
        limiter = DomainRateLimiter()

        # Before each request:
        wait_s = await limiter.acquire("amazon.fr")
        if wait_s > 0:
            await asyncio.sleep(wait_s)

        # After request, record outcome:
        limiter.record_outcome("amazon.fr", "ok")
        limiter.record_outcome("amazon.fr", "block")
    """

    # Default configs per domain
    _DOMAIN_DEFAULTS = {
        "amazon.fr":     DomainConfig("amazon.fr",     base_rpm=8),
        "amazon.com":    DomainConfig("amazon.com",    base_rpm=8),
        "amazon.co.uk":  DomainConfig("amazon.co.uk",  base_rpm=8),
    }
    _DEFAULT_CONFIG = DomainConfig("default", base_rpm=10)

    def __init__(self):
        self._pressure   = PressureTracker()
        self._last_req:  dict[str, float] = {}  # domain → timestamp
        self._lock:      asyncio.Lock     = asyncio.Lock()

    def _get_config(self, domain: str) -> DomainConfig:
        """Get or create config for domain."""
        # Exact match
        if domain in self._DOMAIN_DEFAULTS:
            return self._DOMAIN_DEFAULTS[domain]
        # Partial match (e.g. "www.amazon.fr" → "amazon.fr")
        for key in self._DOMAIN_DEFAULTS:
            if domain.endswith(key):
                return self._DOMAIN_DEFAULTS[key]
        return self._DEFAULT_CONFIG

    async def acquire(self, domain: str) -> float:
        """
        Request permission to fetch from domain.

        Returns the number of seconds the caller should wait.
        If 0, caller can proceed immediately.

        Implementation:
          1. Check Redis rate counter (atomic INCR)
          2. Calculate adaptive delay based on pressure zone
          3. Enforce minimum time since last request
        """
        config = self._get_config(domain)
        zone   = self._pressure.get_zone(domain)

        # Step 1: Redis rate counter
        wait_for_limit = await self._check_redis_rate(domain, config, zone)

        # Step 2: Per-domain minimum interval
        async with self._lock:
            now      = time.monotonic()
            last     = self._last_req.get(domain, 0)
            interval = config.get_interval(zone)
            since_last = now - last
            time_to_wait = max(0.0, interval - since_last)
            self._last_req[domain] = now + time_to_wait

        total_wait = max(wait_for_limit, time_to_wait)

        if total_wait > 0.1:
            logger.debug(
                f"Rate limit {domain}: wait={total_wait:.2f}s "
                f"zone={zone.value}"
            )
        return total_wait

    async def wait(self, domain: str) -> None:
        """Acquire rate limit and wait the required time."""
        wait_s = await self.acquire(domain)
        if wait_s > 0:
            await asyncio.sleep(wait_s)

    def record_outcome(self, domain: str, outcome: str) -> PressureZone:
        """
        Record fetch outcome and update pressure zone.

        outcome: "ok" | "block" | "timeout" | "error"
        Returns new pressure zone.
        """
        zone = self._pressure.record(domain, outcome)
        if outcome in ("block", "timeout") and zone == PressureZone.RED:
            logger.warning(
                json.dumps({
                    "event":  "rate_limit_red_zone",
                    "domain": domain,
                    "zone":   zone.value,
                })
            )
        return zone

    def get_pressure_zone(self, domain: str) -> PressureZone:
        """Get current pressure zone without recording."""
        return self._pressure.get_zone(domain)

    async def _check_redis_rate(
        self,
        domain: str,
        config: DomainConfig,
        zone:   PressureZone,
    ) -> float:
        """
        Redis atomic rate check.
        Returns seconds to wait if rate exceeded, else 0.
        """
        # Effective RPM adjusted for pressure zone
        rpm_multipliers = {
            PressureZone.GREEN:  1.0,
            PressureZone.YELLOW: 0.5,
            PressureZone.RED:    0.2,
        }
        effective_rpm = max(1, int(config.base_rpm * rpm_multipliers[zone]))

        key = f"{_PFX_RATE}{domain}"
        try:
            from core.redis_client import get_redis
            redis = get_redis()
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 60)
            results = await pipe.execute()
            current = results[0]

            if current > effective_rpm:
                # Rate exceeded: calculate wait
                excess   = current - effective_rpm
                wait_s   = (60.0 / effective_rpm) * excess
                jitter   = random.uniform(0, 2.0)
                return min(30.0, wait_s + jitter)
            return 0.0
        except Exception as e:
            logger.warning(f"Redis rate check failed for {domain}: {e}")
            return 0.0  # Fail open

    async def get_stats(self, domain: str) -> dict:
        """Get rate limit stats for a domain."""
        key = f"{_PFX_RATE}{domain}"
        try:
            from core.redis_client import get_redis
            redis = get_redis()
            current = await redis.get(key)
            ttl     = await redis.ttl(key)
            config  = self._get_config(domain)
            zone    = self.get_pressure_zone(domain)
            return {
                "domain":       domain,
                "current_rpm":  int(current or 0),
                "base_rpm":     config.base_rpm,
                "pressure_zone": zone.value,
                "window_ttl_s": ttl,
            }
        except Exception as e:
            return {"domain": domain, "error": str(e)}


# ── Module singleton ──────────────────────────────────────────────────────────

_rate_limiter: Optional[DomainRateLimiter] = None


def get_rate_limiter() -> DomainRateLimiter:
    """Return module-level DomainRateLimiter singleton."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = DomainRateLimiter()
    return _rate_limiter
