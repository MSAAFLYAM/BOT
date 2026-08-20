"""
pinterest/board_manager.py
Board selection by content category + in-memory cache.

Avoids re-searching Pinterest for the same board every publish.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# Content keywords → Board name
BOARD_RULES = [
    (["shoe","sneaker","boot","sandal","slipper"],                "Fashion & Shoes"),
    (["dress","jeans","outfit","shirt","top","sweat","pant"],     "Women's Fashion"),
    (["kitchen","cookware","blender","fryer","pot","pan"],        "Kitchen & Home"),
    (["coffee","espresso","tea","brew"],                          "Coffee & Tea"),
    (["dog","cat","pet","puppy","kitten"],                        "Pet Products"),
    (["fitness","gym","yoga","sport","workout"],                  "Fitness & Health"),
    (["tech","laptop","gadget","electronic","cable","charger"],   "Tech & Gadgets"),
    (["home","decor","garden","organiz","storage"],               "Home & Garden"),
    (["toy","kid","child","baby"],                                "Kids & Toys"),
    (["beauty","makeup","skincare","cosmetic"],                   "Beauty & Skincare"),
]
DEFAULT_BOARD = "My Favorites"


class BoardManager:
    """Selects board name by content + caches resolved board IDs."""

    def __init__(self):
        # name → board_id (resolved on Pinterest)
        self._cache: dict[str, str] = {}

    def get_board_name(self, title: str) -> str:
        """Return best-matching board name for given content title."""
        t = (title or "").lower()
        for keywords, board in BOARD_RULES:
            if any(k in t for k in keywords):
                return board
        return DEFAULT_BOARD

    def get_cached_id(self, board_name: str) -> str | None:
        return self._cache.get(board_name)

    def cache_id(self, board_name: str, board_id: str) -> None:
        if board_name and board_id:
            self._cache[board_name] = board_id
            logger.debug(f"[board] Cached: {board_name} → {board_id}")

    def clear_cache(self) -> None:
        self._cache.clear()


# Module-level singleton
_manager: BoardManager | None = None


def get_board_manager() -> BoardManager:
    global _manager
    if _manager is None:
        _manager = BoardManager()
    return _manager
