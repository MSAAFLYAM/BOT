"""
pinterest/retry_utils.py
Retry helpers for network ops and Playwright selector interactions.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


async def retry_async(
    func:        Callable,
    *args,
    attempts:    int   = 3,
    delay:       float = 2.0,
    backoff:     float = 2.0,
    label:       str   = "op",
    **kwargs,
) -> Any:
    """
    Retry an async function with exponential backoff.

    Raises the last exception if all attempts fail.
    """
    last_err: Optional[Exception] = None
    cur_delay = delay
    for attempt in range(1, attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < attempts:
                logger.debug(f"[retry:{label}] attempt {attempt}/{attempts} failed: {e} — retry in {cur_delay}s")
                await asyncio.sleep(cur_delay)
                cur_delay *= backoff
            else:
                logger.warning(f"[retry:{label}] all {attempts} attempts failed: {e}")
    if last_err:
        raise last_err


async def try_selectors_fill(
    page,
    selectors:   list[str],
    value:       str,
    label:       str   = "field",
    timeout:     int   = 8000,
) -> bool:
    """
    Try a list of selectors in order, fill the first one that exists.
    Returns True if filled, False otherwise.
    """
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.fill(value, timeout=timeout)
                logger.info(f"[pw_pin] {label} filled via: {sel[:40]}")
                return True
        except Exception as e:
            logger.debug(f"[pw_pin] {label} selector failed ({sel[:30]}): {e}")
            continue
    logger.warning(f"[pw_pin] {label}: no selector matched")
    return False


async def try_selectors_click(
    page,
    selectors:   list[str],
    label:       str   = "button",
    timeout:     int   = 8000,
) -> bool:
    """
    Try a list of selectors in order, click the first one that exists.
    Returns True if clicked, False otherwise.
    """
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click(timeout=timeout)
                logger.info(f"[pw_pin] {label} clicked via: {sel[:40]}")
                return True
        except Exception as e:
            logger.debug(f"[pw_pin] {label} click failed ({sel[:30]}): {e}")
            continue
    logger.warning(f"[pw_pin] {label}: no clickable selector matched")
    return False
