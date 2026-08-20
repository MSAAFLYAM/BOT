"""
pinterest/auto_board.py
Auto-create and select Pinterest boards based on content.
No hardcoded board IDs needed.
"""
from __future__ import annotations
import logging, os, re
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

API = "https://api.pinterest.com/v5"

# ── Content → Board mapping ───────────────────────────────────────────────────
BOARD_RULES = [
    (["shoe","sneaker","boot","sandal","slipper"],
     "Fashion & Shoes 👟", "fashion-shoes"),
    (["kitchen","cookware","pot","pan","knife","blender","fryer"],
     "Kitchen Gadgets 🍳", "kitchen-gadgets"),
    (["coffee","espresso","latte","brew"],
     "Coffee & Tea ☕", "coffee-tea"),
    (["fitness","gym","workout","yoga","sport","exercise"],
     "Fitness & Health 💪", "fitness-health"),
    (["dog","cat","pet","puppy","kitten"],
     "Pet Products 🐾", "pet-products"),
    (["home","organiz","storage","decor","garden","outdoor"],
     "Home & Garden 🏡", "home-garden"),
    (["desk","laptop","tech","gadget","electronic","cable"],
     "Tech & Gadgets 💻", "tech-gadgets"),
]

DEFAULT_BOARD = ("My Picks ⭐", "my-picks")


def get_board_for_content(title: str) -> tuple[str, str]:
    """
    Returns (board_name, board_slug) for given content title.
    Auto-selects best matching board based on keywords.
    """
    title_lower = title.lower()
    for keywords, board_name, board_slug in BOARD_RULES:
        if any(kw in title_lower for kw in keywords):
            return board_name, board_slug
    return DEFAULT_BOARD


async def get_or_create_board(
    token:      str,
    board_name: str,
    board_slug: str,
    description: str = "",
) -> Optional[str]:
    """
    Get existing board by name or create a new one.
    Returns board_id or None on failure.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    # ── Try to find existing board ────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{API}/boards",
                headers=headers,
                params={"page_size": 100},
            )
            if resp.status_code == 200:
                boards = resp.json().get("items", [])
                for board in boards:
                    name = board.get("name", "").lower()
                    slug = board.get("id", "")
                    # Match by name similarity
                    if (board_slug.replace("-", " ") in name or
                            name in board_slug.replace("-", " ")):
                        logger.info(f"[pinterest] Found existing board: {board['name']} ({slug})")
                        return board["id"]
    except Exception as e:
        logger.warning(f"[pinterest] Board list error: {e}")

    # ── Create new board ──────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            payload = {
                "name":        board_name,
                "description": description or f"Curated {board_name} picks and recommendations",
                "privacy":     "PUBLIC",
            }
            resp = await client.post(
                f"{API}/boards",
                headers=headers,
                json=payload,
            )
            if resp.status_code in (200, 201):
                board_id = resp.json().get("id", "")
                logger.info(f"[pinterest] ✅ Created board: {board_name} ({board_id})")
                return board_id
            else:
                logger.warning(
                    f"[pinterest] Board create failed: "
                    f"{resp.status_code} {resp.text[:100]}"
                )
    except Exception as e:
        logger.warning(f"[pinterest] Board create error: {e}")

    return None


async def publish_pin(
    token:       str,
    title:       str,
    description: str,
    image_url:   str,
    link:        str = "",
) -> dict:
    """
    Auto-select/create board and publish pin.
    No board ID required — fully automatic.

    Returns: {"success": bool, "pin_id": str, "board": str, "error": str}
    """
    if not token:
        return {"success": False, "error": "PINTEREST_ACCESS_TOKEN not set"}
    if not image_url:
        return {"success": False, "error": "No image URL"}

    # Auto-select board
    board_name, board_slug = get_board_for_content(title)

    # Get or create board
    board_id = await get_or_create_board(token, board_name, board_slug)

    if not board_id:
        return {
            "success": False,
            "error":   f"Could not create board: {board_name}",
        }

    # Publish pin
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{API}/pins",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
                json={
                    "board_id":    board_id,
                    "title":       title[:100],
                    "description": description[:500],
                    "link":        link,
                    "media_source": {
                        "source_type": "image_url",
                        "url":         image_url,
                    },
                },
            )

            if resp.status_code in (200, 201):
                pin_id  = resp.json().get("id", "")
                pin_url = f"https://pinterest.com/pin/{pin_id}/"
                logger.info(f"[pinterest] ✅ Pin created on '{board_name}': {pin_url}")
                return {
                    "success":  True,
                    "pin_id":   pin_id,
                    "pin_url":  pin_url,
                    "board":    board_name,
                }
            elif resp.status_code == 401:
                return {
                    "success": False,
                    "error":   "401 Unauthorized — renew token or request Standard Access",
                }
            else:
                return {
                    "success": False,
                    "error":   f"HTTP {resp.status_code}: {resp.text[:100]}",
                }
    except Exception as e:
        return {"success": False, "error": str(e)[:100]}
