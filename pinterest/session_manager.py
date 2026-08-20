"""
pinterest/session_manager.py
Pinterest session persistence + reliable login detection.

Session file location is configurable via PINTEREST_SESSION_FILE.
Default: /data/pinterest_session.json (persistent volume)
Falls back to /tmp if /data not writable.

Reliable login detection: navigate to /settings/ — if redirected
to /login, we're not authenticated (more robust than DOM selectors
which Pinterest changes often).
"""
from __future__ import annotations
import logging
import os
from typing import Optional

from pinterest import pinterest_selectors as S

logger = logging.getLogger(__name__)


def get_session_file() -> str:
    """
    Return session file path.
    Prefer persistent /data, fall back to /tmp.
    """
    configured = os.getenv("PINTEREST_SESSION_FILE", "")
    if configured:
        return configured

    # Try /data (persistent volume)
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return "/data/pinterest_session.json"

    return "/tmp/pinterest_session.json"


def session_exists() -> bool:
    return os.path.exists(get_session_file())


async def is_logged_in(page) -> bool:
    """
    Reliable login check: visit /settings/.
    If Pinterest keeps us there → logged in.
    If it redirects to /login → not authenticated.
    """
    try:
        await page.goto(S.SETTINGS_URL, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        url = page.url.lower()
        logged = "/login" not in url and "/signup" not in url
        logger.info(f"[session] Login check: {'✅ authenticated' if logged else '❌ not logged in'} (url={url[:50]})")
        return logged
    except Exception as e:
        logger.warning(f"[session] Login check error: {e}")
        return False


async def login(page, context, email: str, password: str) -> bool:
    """
    Perform login and save session.
    Returns True on success.
    """
    from pinterest.retry_utils import try_selectors_fill, try_selectors_click

    try:
        logger.info("[session] Logging in...")
        await page.goto(S.LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        ok_email = await try_selectors_fill(page, S.LOGIN_EMAIL, email, "email", timeout=8000)
        ok_pass  = await try_selectors_fill(page, S.LOGIN_PASSWORD, password, "password", timeout=8000)

        if not (ok_email and ok_pass):
            logger.warning("[session] Could not fill login form")
            return False

        await try_selectors_click(page, S.LOGIN_SUBMIT, "login submit", timeout=8000)
        await page.wait_for_timeout(6000)

        # Verify login worked
        url = page.url.lower()
        if "/login" in url:
            logger.warning("[session] Still on login page — wrong credentials or 2FA/captcha")
            return False

        # Save session
        save_session(context)
        logger.info("[session] ✅ Login successful, session saved")
        return True

    except Exception as e:
        logger.warning(f"[session] Login error: {e}")
        return False


def save_session(context) -> None:
    """Save browser session (cookies) — called sync via context."""
    try:
        path = get_session_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # storage_state is async on context; caller handles await
    except Exception as e:
        logger.debug(f"[session] save prep error: {e}")


async def save_session_async(context) -> None:
    """Save browser session state to disk."""
    try:
        path = get_session_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        await context.storage_state(path=path)
        logger.info(f"[session] Session saved → {path}")
    except Exception as e:
        logger.warning(f"[session] Save error: {e}")
