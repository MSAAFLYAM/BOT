"""
pinterest/py3_publisher.py
Publish pins using py3-pinterest (email+password auth).
No Standard Access needed. Fully automatic.

pip install py3-pinterest

Environment Variables:
  PINTEREST_EMAIL    = ton@email.com
  PINTEREST_PASSWORD = ton_password
"""
from __future__ import annotations
import logging, os, re
from typing import Optional

logger = logging.getLogger(__name__)

# Board name → auto-create if not exists
BOARD_RULES = [
    (["shoe","sneaker","boot","sandal"],                 "Fashion & Shoes"),
    (["kitchen","cookware","blender","fryer","coffee"],  "Kitchen & Home"),
    (["dog","cat","pet","puppy"],                        "Pet Products"),
    (["fitness","gym","yoga","sport"],                   "Fitness & Health"),
    (["tech","laptop","gadget","electronic"],            "Tech & Gadgets"),
]
DEFAULT_BOARD = "Amazon Deals"


def _get_board_name(title: str) -> str:
    t = title.lower()
    for keywords, board in BOARD_RULES:
        if any(k in t for k in keywords):
            return board
    return DEFAULT_BOARD


def publish_pin_py3(
    title:       str,
    description: str,
    image_url:   str,
    link:        str = "",
) -> dict:
    """
    Publish pin using py3-pinterest (sync).
    Returns {"success": bool, "board": str, "error": str}
    """
    email    = os.environ.get("PINTEREST_EMAIL", "")
    password = os.environ.get("PINTEREST_PASSWORD", "")

    if not email or not password:
        return {
            "success": False,
            "error":   "PINTEREST_EMAIL or PINTEREST_PASSWORD not set",
        }

    try:
        from pinterest.api import Pinterest

        p = Pinterest(
            email    = email,
            password = password,
            ciphers  = "HIGH:!DH:!aNULL",
        )

        # Login
        if not p.login():
            return {"success": False, "error": "Login failed — check email/password"}

        board_name = _get_board_name(title)

        # Get or create board
        board_id = _get_or_create_board(p, board_name)

        if not board_id:
            return {"success": False, "error": f"Could not find/create board: {board_name}"}

        # Create pin
        result = p.create_pin(
            board_id  = board_id,
            image_url = image_url,
            description = f"{title}\n\n{description[:300]}",
            link      = link,
        )

        if result and result.get("id"):
            pin_id  = result["id"]
            pin_url = f"https://pinterest.com/pin/{pin_id}/"
            logger.info(f"[py3pin] ✅ Pin on '{board_name}': {pin_url}")
            return {
                "success":  True,
                "board":    board_name,
                "pin_id":   pin_id,
                "pin_url":  pin_url,
            }
        else:
            return {"success": False, "error": f"Pin creation returned: {result}"}

    except ImportError:
        return {
            "success": False,
            "error":   "py3-pinterest not installed — pip install py3-pinterest",
        }
    except Exception as e:
        logger.warning(f"[py3pin] Error: {e}")
        return {"success": False, "error": str(e)[:150]}


def _get_or_create_board(p, board_name: str) -> Optional[str]:
    """Find existing board or create new one."""
    try:
        # Get all boards
        boards = p.get_boards(p.get_me()["data"]["id"])
        if boards and "data" in boards:
            for board in boards["data"]:
                if board.get("name","").lower() == board_name.lower():
                    return board["id"]

        # Create new board
        new_board = p.create_board(board_name, privacy="public")
        if new_board and "data" in new_board:
            bid = new_board["data"]["id"]
            logger.info(f"[py3pin] Created board: {board_name} ({bid})")
            return bid

    except Exception as e:
        logger.warning(f"[py3pin] Board error: {e}")

    return None
