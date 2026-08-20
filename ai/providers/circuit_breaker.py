"""
ai/providers/circuit_breaker.py — Redis-backed Circuit Breaker.

States:
  CLOSED    → normal, requests pass through
  OPEN      → provider disabled (cooldown after N failures)
  HALF_OPEN → one test request allowed after cooldown

Architecture decisions:
  - State stored in Redis (survives restarts, shared across Celery workers)
  - Per-provider independent breaker (Groq failure ≠ Gemini failure)
  - Configurable thresholds (failure_threshold, cooldown_s)
  - No external library needed — pure Python + Redis

Usage:
    cb = CircuitBreaker("groq")

    if cb.is_open():
        # Skip this provider
        continue

    try:
        result = await provider.generate(prompt)
        cb.record_success()
    except Exception as e:
        cb.record_failure()
        raise
"""
from __future__ import annotations

import json
import logging
import os
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

_PREFIX = "circuit_breaker:"


class State(str, Enum):
    CLOSED    = "closed"      # Normal — requests pass through
    OPEN      = "open"        # Disabled — requests blocked
    HALF_OPEN = "half_open"   # Testing — one request allowed


class CircuitBreaker:
    """
    Per-provider circuit breaker backed by Redis.

    Args:
        provider_name:     Name of the provider ("groq", "gemini", etc.)
        failure_threshold: Failures before opening (default: 3)
        cooldown_s:        Seconds to stay OPEN before HALF_OPEN (default: 300)
        success_threshold: Successes in HALF_OPEN to close (default: 1)
    """

    def __init__(
        self,
        provider_name:     str,
        failure_threshold: int = 3,
        cooldown_s:        int = 300,
        success_threshold: int = 1,
    ):
        self.provider        = provider_name
        self.failure_threshold = failure_threshold
        self.cooldown_s      = cooldown_s
        self.success_threshold = success_threshold
        self._key            = f"{_PREFIX}{provider_name}"

    def _get_redis(self):
        from core.safe_redis import get_safe_redis
        return get_safe_redis()

    # ── State management ──────────────────────────────────────────────────────

    def _get_state(self) -> dict:
        """Load current state from Redis. Returns default if not found."""
        default = {
            "state":           State.CLOSED,
            "failure_count":   0,
            "success_count":   0,
            "opened_at":       0.0,
            "last_failure_at": 0.0,
        }
        from core.safe_redis import safe_get
        data = safe_get(self._key)
        if data:
            try:
                return json.loads(data)
            except Exception:
                pass
        return default

    def _save_state(self, state: dict) -> None:
        """Persist state to Redis with TTL."""
        from core.safe_redis import safe_set
        ttl = max(self.cooldown_s * 3, 3600)
        safe_set(self._key, json.dumps(state), ttl=ttl)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_state(self) -> State:
        """Return current circuit state."""
        s = self._get_state()
        current_state = s.get("state", State.CLOSED)

        # Auto-transition: OPEN → HALF_OPEN after cooldown
        if current_state == State.OPEN:
            opened_at = s.get("opened_at", 0)
            if time.time() - opened_at >= self.cooldown_s:
                logger.info(
                    f"[cb:{self.provider}] OPEN → HALF_OPEN "
                    f"(cooldown {self.cooldown_s}s elapsed)"
                )
                s["state"]         = State.HALF_OPEN
                s["success_count"] = 0
                self._save_state(s)
                return State.HALF_OPEN

        return State(current_state)

    def is_open(self) -> bool:
        """True if provider should be SKIPPED (circuit is OPEN)."""
        return self.get_state() == State.OPEN

    def can_attempt(self) -> bool:
        """True if a request can be attempted."""
        state = self.get_state()
        if state == State.CLOSED:
            return True
        if state == State.HALF_OPEN:
            return True   # One test request allowed
        return False     # OPEN → skip

    def record_success(self) -> None:
        """Record a successful provider call."""
        s     = self._get_state()
        state = State(s.get("state", State.CLOSED))

        if state == State.HALF_OPEN:
            s["success_count"] = s.get("success_count", 0) + 1
            if s["success_count"] >= self.success_threshold:
                # Recovery confirmed → CLOSED
                logger.info(
                    f"[cb:{self.provider}] ✅ HALF_OPEN → CLOSED "
                    f"({s['success_count']} successes)"
                )
                s["state"]         = State.CLOSED
                s["failure_count"] = 0
                s["success_count"] = 0
        elif state == State.CLOSED:
            # Reset failure count on success
            if s.get("failure_count", 0) > 0:
                s["failure_count"] = max(0, s["failure_count"] - 1)

        self._save_state(s)

    def record_failure(self) -> None:
        """Record a failed provider call."""
        s     = self._get_state()
        state = State(s.get("state", State.CLOSED))

        s["failure_count"]   = s.get("failure_count", 0) + 1
        s["last_failure_at"] = time.time()

        if state == State.HALF_OPEN:
            # Test failed → back to OPEN
            logger.warning(
                f"[cb:{self.provider}] ❌ HALF_OPEN → OPEN "
                f"(test request failed)"
            )
            s["state"]     = State.OPEN
            s["opened_at"] = time.time()

        elif state == State.CLOSED:
            if s["failure_count"] >= self.failure_threshold:
                # Too many failures → OPEN
                logger.warning(
                    f"[cb:{self.provider}] ❌ CLOSED → OPEN "
                    f"({s['failure_count']} failures, "
                    f"cooldown {self.cooldown_s}s)"
                )
                s["state"]     = State.OPEN
                s["opened_at"] = time.time()

        self._save_state(s)

    def reset(self) -> None:
        """Manually reset circuit to CLOSED state."""
        self._save_state({
            "state":           State.CLOSED,
            "failure_count":   0,
            "success_count":   0,
            "opened_at":       0.0,
            "last_failure_at": 0.0,
        })
        logger.info(f"[cb:{self.provider}] Manually reset → CLOSED")

    def get_status(self) -> dict:
        """Return full status dict for monitoring."""
        s     = self._get_state()
        state = self.get_state()
        now   = time.time()

        result = {
            "provider":       self.provider,
            "state":          state.value,
            "failure_count":  s.get("failure_count", 0),
            "threshold":      self.failure_threshold,
            "cooldown_s":     self.cooldown_s,
        }

        if state == State.OPEN:
            opened_at  = s.get("opened_at", now)
            elapsed    = now - opened_at
            remaining  = max(0, self.cooldown_s - elapsed)
            result["open_for_s"]      = round(elapsed, 1)
            result["reopen_in_s"]     = round(remaining, 1)

        if s.get("last_failure_at"):
            result["last_failure_ago_s"] = round(now - s["last_failure_at"], 1)

        return result


# ── Registry ──────────────────────────────────────────────────────────────────

_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(
    provider_name: str,
    failure_threshold: int = 3,
    cooldown_s: int = 300,
) -> CircuitBreaker:
    """
    Return (or create) a CircuitBreaker for the given provider.
    Singleton per provider name.
    """
    if provider_name not in _breakers:
        _breakers[provider_name] = CircuitBreaker(
            provider_name,
            failure_threshold=failure_threshold,
            cooldown_s=cooldown_s,
        )
    return _breakers[provider_name]


def get_all_statuses() -> list[dict]:
    """Return status of all registered circuit breakers."""
    return [cb.get_status() for cb in _breakers.values()]
