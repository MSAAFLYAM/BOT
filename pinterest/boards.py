"""
pinterest/boards.py — Board management with auto-creation.
pinterest/scheduler.py — Daily cap enforcement via Redis.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# BOARDS
# ══════════════════════════════════════════════════════════════════

# Board names per content type
DEFAULT_BOARDS = {
    "product":   "Bons Plans Amazon",
    "article":   "Articles & Guides",
    "comparison":"Comparatifs Produits",
    "guide":     "Guides d'Achat",
}

_BOARD_CACHE_KEY = "pinterest:boards:cache"
_BOARD_CACHE_TTL = 3600  # 1 hour


class BoardManager:
    """
    Manage Pinterest boards with caching.

    Usage:
        manager = BoardManager()
        board_id = await manager.get_or_create_board("product")
        # Returns board ID for content type, creating if needed.
    """

    def __init__(self):
        from pinterest.client import get_pinterest_client
        self._client = get_pinterest_client()

    async def get_or_create_board(self, content_type: str) -> Optional[str]:
        """
        Get board ID for content type. Create if doesn't exist.

        content_type: "product" | "article" | "comparison" | "guide"
        Returns board ID string or None on failure.
        """
        board_name = DEFAULT_BOARDS.get(content_type, DEFAULT_BOARDS["article"])

        # Check cache first
        cached_id = await self._get_from_cache(board_name)
        if cached_id:
            return cached_id

        # Search in existing boards
        try:
            board = await self._client.get_board_by_name(board_name)
            if board:
                board_id = board["id"]
                await self._set_cache(board_name, board_id)
                logger.debug(f"[boards] Found: {board_name} → {board_id}")
                return board_id

            # Create new board
            logger.info(f"[boards] Creating board: {board_name}")
            new_board = await self._client.create_board(
                name=board_name,
                description=self._get_description(content_type),
                privacy="PUBLIC",
            )
            board_id = new_board["id"]
            await self._set_cache(board_name, board_id)
            logger.info(f"[boards] Created: {board_name} → {board_id}")
            return board_id

        except Exception as e:
            logger.error(f"[boards] Failed for {content_type}: {e}")
            return None

    def _get_description(self, content_type: str) -> str:
        descriptions = {
            "product":   "Les meilleures offres et bons plans Amazon",
            "article":   "Articles, guides et conseils pratiques",
            "comparison":"Comparatifs et tests produits détaillés",
            "guide":     "Guides d'achat pour faire le bon choix",
        }
        return descriptions.get(content_type, "")

    async def _get_from_cache(self, board_name: str) -> Optional[str]:
        from core.safe_redis import safe_get
        return safe_get(f"{_BOARD_CACHE_KEY}:{board_name}")

    async def _set_cache(self, board_name: str, board_id: str) -> None:
        from core.safe_redis import safe_set
        safe_set(f"{_BOARD_CACHE_KEY}:{board_name}", board_id, ttl=_BOARD_CACHE_TTL)

    async def list_all(self) -> list[dict]:
        """List all boards with their content types."""
        try:
            boards = await self._client.list_boards()
            result = []
            for board in boards:
                content_type = next(
                    (k for k, v in DEFAULT_BOARDS.items()
                     if v.lower() == board.get("name","").lower()),
                    "unknown"
                )
                result.append({
                    "id":           board.get("id"),
                    "name":         board.get("name"),
                    "content_type": content_type,
                    "pin_count":    board.get("pin_count", 0),
                })
            return result
        except Exception as e:
            logger.error(f"[boards] List failed: {e}")
            return []


# ══════════════════════════════════════════════════════════════════
# SCHEDULER — Daily cap enforcement
# ══════════════════════════════════════════════════════════════════

_DAILY_COUNT_KEY = "pinterest:daily:count"
_DAILY_LOG_KEY   = "pinterest:daily:log"


class DailyScheduler:
    """
    Enforce daily pin cap using Redis counters.

    Counter resets automatically at midnight (TTL = seconds until midnight).
    Logs each pin (entity_id, type, timestamp) for analytics.

    Usage:
        scheduler = DailyScheduler(daily_cap=5)

        if await scheduler.can_pin_today():
            await scheduler.record_pin("product", "uuid-123")
        else:
            logger.info("Daily cap reached, will retry tomorrow")
    """

    def __init__(self, daily_cap: Optional[int] = None):
        if daily_cap:
            self._cap = daily_cap
        else:
            import os
            try:
                self._cap = int(os.environ.get("PINTEREST_DAILY_CAP", "5"))
            except Exception:
                self._cap = 5

    def _seconds_until_midnight(self) -> int:
        """Seconds until next midnight UTC."""
        now  = datetime.now(timezone.utc)
        midnight = now.replace(
            hour=23, minute=59, second=59,
            microsecond=0
        )
        if now > midnight:
            return 86400
        return int((midnight - now).total_seconds()) + 1

    async def get_today_count(self) -> int:
        """Return number of pins created today."""
        from core.safe_redis import safe_get
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        val   = safe_get(f"{_DAILY_COUNT_KEY}:{today}", "0")
        try:
            return int(val)
        except Exception:
            return 0

    async def can_pin_today(self) -> bool:
        """True if daily cap not yet reached."""
        count = await self.get_today_count()
        return count < self._cap

    async def get_remaining_today(self) -> int:
        """Return number of pins still allowed today."""
        count = await self.get_today_count()
        return max(0, self._cap - count)

    async def record_pin(
        self,
        content_type: str,
        entity_id:    str,
        pin_id:       str = "",
        board_id:     str = "",
    ) -> bool:
        """
        Record a pin in the daily counter.
        Returns True if recorded, False if cap exceeded.
        """
        if not await self.can_pin_today():
            return False

        try:
            from core.safe_redis import safe_incr, safe_rpush
            today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            ttl       = self._seconds_until_midnight()
            count_key = f"{_DAILY_COUNT_KEY}:{today}"
            safe_incr(count_key, ttl=ttl)

            log_entry = json.dumps({
                "ts":           datetime.now(timezone.utc).isoformat(),
                "content_type": content_type,
                "entity_id":    entity_id,
                "pin_id":       pin_id,
                "board_id":     board_id,
            })
            safe_rpush(f"{_DAILY_LOG_KEY}:{today}", log_entry, ttl=ttl + 86400)
            return True

        except Exception as e:
            logger.warning(f"[scheduler] Record failed: {e}")
            return True  # Fail open

    async def get_today_log(self) -> list[dict]:
        """Get list of pins created today."""
        from core.safe_redis import safe_lrange
        today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entries = safe_lrange(f"{_DAILY_LOG_KEY}:{today}", 0, -1)
        result  = []
        for e in entries:
            try:
                result.append(json.loads(e if isinstance(e, str) else e.decode()))
            except Exception:
                pass
        return result

    async def get_stats(self) -> dict:
        """Return daily pin statistics."""
        count     = await self.get_today_count()
        remaining = max(0, self._cap - count)
        log       = await self.get_today_log()
        today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "date":       today,
            "daily_cap":  self._cap,
            "pins_today": count,
            "remaining":  remaining,
            "cap_reached":count >= self._cap,
            "log":        log,
        }
