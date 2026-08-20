"""
pinterest/pin_creator.py
Core pin creation logic — upload image, fill fields, select board, publish.
"""
from __future__ import annotations
import logging
import os

from pinterest import pinterest_selectors as S
from pinterest.retry_utils import try_selectors_fill, try_selectors_click
from pinterest.board_manager import get_board_manager

logger = logging.getLogger(__name__)

DEBUG_DIR = os.getenv("DEBUG_SCREENSHOT_DIR", "/tmp/pinterest_debug")


async def _save_debug(page, label: str) -> None:
    """Save screenshot + HTML on failure for debugging."""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        img  = f"{DEBUG_DIR}/{label}.png"
        html = f"{DEBUG_DIR}/{label}.html"
        await page.screenshot(path=img, full_page=True)
        content = await page.content()
        with open(html, "w", encoding="utf-8") as f:
            f.write(content)
        logger.warning(f"[pin] Debug saved → {img}")
    except Exception as e:
        logger.debug(f"[pin] Debug save error: {e}")


async def upload_image(page, img_path: str) -> bool:
    """
    Upload image via 2-strategy approach.
    Returns True on success.
    """
    # Strategy 1: hidden file input (no visibility wait)
    for sel in S.FILE_INPUT:
        try:
            loc = page.locator(sel)
            cnt = await loc.count()
            if cnt > 0:
                await loc.first.set_input_files(img_path, timeout=15000)
                logger.info("[pin] ✅ Upload via hidden file input")
                return True
        except Exception as e:
            logger.debug(f"[pin] hidden input failed: {e}")

    # Strategy 2: click upload button → file chooser
    try:
        async with page.expect_file_chooser(timeout=15000) as fc:
            clicked = await try_selectors_click(
                page, S.UPLOAD_BUTTONS, "upload button", timeout=5000
            )
            if not clicked:
                raise Exception("No upload button found")
        chooser = await fc.value
        await chooser.set_files(img_path)
        logger.info("[pin] ✅ Upload via file chooser")
        return True
    except Exception as e:
        logger.debug(f"[pin] file chooser failed: {e}")

    await _save_debug(page, "upload_failed")
    return False


async def fill_fields(page, title: str, description: str, link: str) -> None:
    """Fill title, description, link fields."""
    await try_selectors_fill(page, S.TITLE_FIELDS, title[:100], "title")
    await try_selectors_fill(page, S.DESC_FIELDS, description[:400], "description")
    if link:
        await try_selectors_fill(page, S.LINK_FIELDS, link, "link")


async def select_or_create_board(page, board_name: str) -> bool:
    """
    Select existing board or create it.
    Uses BoardManager cache to avoid repeated searches.
    Returns True if board was set.
    """
    bm = get_board_manager()

    # Open board dropdown
    clicked = await try_selectors_click(page, S.BOARD_DROPDOWN, "board dropdown")
    if not clicked:
        logger.warning("[pin] Board dropdown not found — using default board")
        return False

    import asyncio
    await asyncio.sleep(1500 / 1000)

    # Search for board
    await try_selectors_fill(page, S.BOARD_SEARCH, board_name, "board search", timeout=3000)
    await asyncio.sleep(1500 / 1000)

    # Click matching board option
    board_sel = S.BOARD_OPTION.format(name=board_name)
    try:
        opt = page.locator(board_sel).first
        if await opt.count() > 0:
            await opt.click(timeout=5000)
            logger.info(f"[pin] ✅ Board selected: {board_name}")
            return True
    except Exception:
        pass

    # Board not found → create
    logger.info(f"[pin] Board '{board_name}' not found — creating...")
    clicked_create = await try_selectors_click(page, S.BOARD_CREATE, "create board", timeout=5000)
    if clicked_create:
        await asyncio.sleep(2000 / 1000)
        # Fill board name in creation dialog
        await try_selectors_fill(
            page,
            ['input[placeholder*="board name" i]', 'input[aria-label*="name" i]'],
            board_name, "new board name", timeout=5000
        )
        await asyncio.sleep(1000 / 1000)
        await try_selectors_click(
            page,
            ['button:has-text("Create")', 'button[type="submit"]'],
            "confirm create board"
        )
        await asyncio.sleep(2000 / 1000)
        logger.info(f"[pin] ✅ Board created: {board_name}")
        return True

    return False


async def click_publish(page) -> bool:
    """Click the publish/save button. Returns True if clicked."""
    import asyncio
    await asyncio.sleep(2000 / 1000)
    clicked = await try_selectors_click(page, S.PUBLISH_BUTTONS, "publish button", timeout=10000)
    if clicked:
        await asyncio.sleep(6000 / 1000)
    else:
        await _save_debug(page, "publish_failed")
    return clicked


async def create_pin(
    page,
    title:       str,
    description: str,
    image_path:  str,
    link:        str,
    board_name:  str,
) -> dict:
    """
    Full pin creation flow.
    Returns {"success": bool, "board": str, "error": str}
    """
    import asyncio

    # Navigate to creation tool
    from pinterest.pinterest_selectors import CREATE_URL
    await page.goto(CREATE_URL, timeout=45000, wait_until="domcontentloaded")
    await asyncio.sleep(7000 / 1000)

    # Check we're not redirected to login
    if "/login" in page.url.lower() or "/signup" in page.url.lower():
        return {"success": False, "error": "Redirected to login — session expired"}

    # Upload image
    if not await upload_image(page, image_path):
        return {"success": False, "error": "Image upload failed — Pinterest UI may have changed"}
    await asyncio.sleep(6000 / 1000)

    # Fill fields
    await fill_fields(page, title, description, link)
    await asyncio.sleep(2000 / 1000)

    # Select/create board
    await select_or_create_board(page, board_name)
    await asyncio.sleep(1000 / 1000)

    # Publish
    published = await click_publish(page)

    return {
        "success": published,
        "board":   board_name,
        "error":   "" if published else "Publish button not found",
    }
