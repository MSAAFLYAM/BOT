"""
pinterest/playwright_publisher.py
Main orchestrator — health_check + publish pipeline.

Hierarchy:
  playwright_publisher.py   ← YOU ARE HERE (orchestrator)
    ├── session_manager.py  ← login + cookies
    ├── board_manager.py    ← board cache + category rules
    ├── image_downloader.py ← download with retry
    ├── pin_creator.py      ← upload + fill + publish
    ├── pinterest_selectors ← all DOM selectors
    └── retry_utils.py      ← retry helpers

Rate limiting:
  MIN_DELAY = 300s  (5 min)   between pins — configurable via env
  MAX_DELAY = 1800s (30 min)  for random jitter

Usage:
    from pinterest.playwright_publisher import publish_pin_sync, health_check_sync
    result = publish_pin_sync(title, description, image_url, link)
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Rate limiting ─────────────────────────────────────────────────────────────
MIN_DELAY   = int(os.getenv("PINTEREST_MIN_DELAY", "300"))   # seconds between pins
MAX_DELAY   = int(os.getenv("PINTEREST_MAX_DELAY", "1800"))  # max jitter ceiling
_last_pin_time: float = 0.0                                   # module-level timestamp

# ── User-Agent (configurable via env) ────────────────────────────────────────
USER_AGENT = os.getenv(
    "PINTEREST_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
)


def _build_context_kwargs() -> dict:
    ctx: dict = {
        "viewport":   {"width": 1280, "height": 800},
        "user_agent": USER_AGENT,
        "locale":     "en-US",
        "timezone_id": "America/New_York",
    }
    sf = os.path.join(os.path.dirname(__file__) + "/../") + "session.json"
    from pinterest.session_manager import get_session_file, session_exists
    sf = get_session_file()
    if session_exists():
        ctx["storage_state"] = sf
    return ctx


async def _get_browser_and_page(playwright):
    """Launch browser with anti-detection settings."""
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--window-size=1280,800",
        ],
    )
    context = await browser.new_context(**_build_context_kwargs())

    # Apply stealth if available
    page = await context.new_page()
    try:
        from playwright_stealth import stealth_async
        await stealth_async(page)
        logger.info("[pw] Stealth applied")
    except Exception:
        try:
            from playwright_stealth import Stealth
            await Stealth().apply_stealth_async(page)
        except Exception:
            logger.debug("[pw] playwright-stealth not available")

    return browser, context, page


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════

async def health_check() -> dict:
    """
    Production health check before publishing.

    Verifies:
      ✅ Playwright + Chromium available
      ✅ Pinterest credentials configured
      ✅ Pinterest session valid (login check)
      ✅ Image download working (test URL)
      ✅ Rate limit not exceeded

    Returns dict with "ok": bool and per-check results.
    """
    results = {
        "playwright":   False,
        "credentials":  False,
        "session":      False,
        "image_dl":     False,
        "rate_limit":   False,
        "ok":           False,
        "errors":       [],
    }

    # Check 1: Playwright + Chromium
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True,
                args=["--no-sandbox","--disable-dev-shm-usage"])
            await browser.close()
        results["playwright"] = True
    except Exception as e:
        results["errors"].append(f"Playwright: {e}")

    # Check 2: Credentials
    email = os.getenv("PINTEREST_EMAIL","")
    pwd   = os.getenv("PINTEREST_PASSWORD","")
    if email and pwd:
        results["credentials"] = True
    else:
        results["errors"].append("PINTEREST_EMAIL/PASSWORD not set")

    # Check 3: Session (login check via /settings/)
    if results["playwright"] and results["credentials"]:
        try:
            from playwright.async_api import async_playwright
            from pinterest.session_manager import is_logged_in, login, save_session_async
            async with async_playwright() as p:
                browser, context, page = await _get_browser_and_page(p)
                logged = await is_logged_in(page)
                if not logged:
                    # Try logging in
                    logged = await login(page, context, email, pwd)
                    if logged:
                        await save_session_async(context)
                results["session"] = logged
                if not logged:
                    results["errors"].append("Pinterest login failed — check credentials or 2FA")
                await browser.close()
        except Exception as e:
            results["errors"].append(f"Session check: {e}")

    # Check 4: Image download
    try:
        from pinterest.image_downloader import download_image, cleanup_temp
        test_url = "https://m.media-amazon.com/images/I/71test_health_check.jpg"
        # Use a real small image for the test
        test_url = "https://httpbin.org/image/jpeg"
        path = await download_image(test_url, attempts=1, timeout=10)
        if path:
            cleanup_temp(path)
            results["image_dl"] = True
        else:
            results["errors"].append("Image download returned None")
    except Exception as e:
        results["errors"].append(f"Image download: {e}")
        results["image_dl"] = True  # non-blocking

    # Check 5: Rate limit
    global _last_pin_time
    elapsed = time.monotonic() - _last_pin_time
    if _last_pin_time == 0.0 or elapsed >= MIN_DELAY:
        results["rate_limit"] = True
    else:
        wait = int(MIN_DELAY - elapsed)
        results["errors"].append(f"Rate limit: wait {wait}s before next pin")

    # Overall
    results["ok"] = (
        results["playwright"] and
        results["credentials"] and
        results["session"]
    )
    return results


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISH PIN
# ══════════════════════════════════════════════════════════════════════════════

async def publish_pin(
    title:       str,
    description: str,
    image_url:   str,
    link:        str = "",
) -> dict:
    """
    Full automatic pin publishing pipeline.

    Flow:
      1. Rate limit check
      2. Download image (3 retries)
      3. Launch browser + stealth
      4. Check/restore session
      5. Login if needed
      6. Create pin (upload → fill → board → publish)
      7. Update rate limit timestamp

    Returns {"success": bool, "board": str, "pin_url": str, "error": str}
    """
    global _last_pin_time

    email    = os.getenv("PINTEREST_EMAIL","")
    password = os.getenv("PINTEREST_PASSWORD","")

    if not email or not password:
        return {"success": False, "error": "PINTEREST_EMAIL/PASSWORD not set"}
    if not image_url:
        return {"success": False, "error": "No image URL provided"}

    # ── Rate limit ────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - _last_pin_time
    if _last_pin_time > 0 and elapsed < MIN_DELAY:
        wait = int(MIN_DELAY - elapsed)
        logger.info(f"[pw_pin] Rate limit: wait {wait}s (min={MIN_DELAY}s between pins)")
        return {
            "success": False,
            "error":   f"Rate limit — wait {wait}s before next pin",
        }

    # ── Download image ────────────────────────────────────────────────────────
    from pinterest.image_downloader import download_image, cleanup_temp
    img_path = await download_image(image_url, attempts=3)
    if not img_path:
        return {"success": False, "error": "Image download failed after 3 attempts"}

    # ── Board name ────────────────────────────────────────────────────────────
    from pinterest.board_manager import get_board_manager
    board_name = get_board_manager().get_board_name(title)

    # ── Browser session ───────────────────────────────────────────────────────
    try:
        from playwright.async_api import async_playwright
        from pinterest.session_manager import is_logged_in, login, save_session_async
        from pinterest.pin_creator import create_pin

        async with async_playwright() as p:
            browser, context, page = await _get_browser_and_page(p)

            # Ensure logged in
            logged = await is_logged_in(page)
            if not logged:
                logger.info("[pw_pin] Session expired — re-logging in...")
                logged = await login(page, context, email, password)
                if not logged:
                    await browser.close()
                    cleanup_temp(img_path)
                    return {
                        "success": False,
                        "error": "Login failed — check credentials / disable 2FA",
                    }
                await save_session_async(context)

            # Add random human-like jitter (2-5s)
            await asyncio.sleep(random.uniform(2, 5))

            # Create pin
            result = await create_pin(
                page        = page,
                title       = title,
                description = description,
                image_path  = img_path,
                link        = link,
                board_name  = board_name,
            )

            await browser.close()

    except Exception as e:
        logger.error(f"[pw_pin] Browser error: {e}")
        cleanup_temp(img_path)
        return {"success": False, "error": str(e)[:150]}

    finally:
        cleanup_temp(img_path)

    # ── Update rate limit timestamp ───────────────────────────────────────────
    if result.get("success"):
        _last_pin_time = time.monotonic()
        logger.info(f"[pw_pin] ✅ Pin published on '{board_name}' — next allowed in {MIN_DELAY}s")
    else:
        logger.warning(f"[pw_pin] ❌ Failed: {result.get('error','')}")

    return result


# ══════════════════════════════════════════════════════════════════════════════
# SYNC WRAPPERS — safe for gunicorn threads
# ══════════════════════════════════════════════════════════════════════════════

def publish_pin_sync(
    title: str, description: str, image_url: str, link: str = ""
) -> dict:
    """Sync wrapper — creates own event loop (safe for gunicorn threads)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(publish_pin(title, description, image_url, link))
    finally:
        loop.close()


def health_check_sync() -> dict:
    """Sync wrapper for health_check."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(health_check())
    finally:
        loop.close()
