"""
scraping/browser/pool.py — Persistent browser pool.

CRITICAL ARCHITECTURE RULE (from spec):
  DO NOT launch a browser for every request.
  Browsers are expensive: ~200-400MB RAM, ~3-5s startup time.
  A browser-per-request would cause OOM in memory-constrained deployments.

This module implements:
  - A fixed pool of browser CONTEXTS (not browsers per request).
  - Contexts are pre-warmed at startup and reused across requests.
  - Pages are created from reused contexts (< 10ms overhead vs 3-5s for new browser).
  - Idle contexts are health-checked and recycled automatically.
  - Semaphore limits concurrent page creation (memory safety).

Pool lifecycle:
  1. initialize() — called once at startup (or on first use, lazy init)
     → launches N browsers, creates M contexts per browser
  2. acquire_page() — caller gets a Page from the pool
     → Page is closed after use, Context is returned to pool
  3. recycle_context() — called when context becomes unhealthy
     → old context closed, new one created
  4. shutdown() — graceful cleanup

Memory optimization for constrained deployments (512MB):
  max_browsers=1, max_contexts=1 → 1 active Playwright page at a time
  This uses ~250MB, leaving 250MB for the Python process + other services.

Stealth configuration (per spec):
  - navigator.webdriver masked
  - chrome object injected
  - plugins array spoofed
  - language headers set
  - viewport randomized slightly per context
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# ── Stealth init script ───────────────────────────────────────────────────────
# Injected into every new browser context to mask automation signals.

_STEALTH_SCRIPT = """
// Mask webdriver flag
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true,
});

// Inject Chrome runtime object
window.chrome = {
    runtime: {
        connect: () => {},
        sendMessage: () => {},
    },
    loadTimes: () => ({}),
    csi: () => ({}),
    app: { isInstalled: false },
};

// Spoof plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'Chrome PDF Plugin', description: 'Portable Document Format', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', description: '', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', description: '', filename: 'internal-nacl-plugin' },
        ];
        plugins.refresh = () => {};
        plugins.item    = (i) => plugins[i];
        plugins.namedItem = (n) => plugins.find(p => p.name === n);
        Object.setPrototypeOf(plugins, PluginArray.prototype);
        return plugins;
    },
    configurable: true,
});

// Spoof language (matches Accept-Language header)
Object.defineProperty(navigator, 'languages', {
    get: () => ['fr-FR', 'fr', 'en-US', 'en'],
    configurable: true,
});

// Spoof permissions API
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => {
    if (parameters.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission });
    }
    return originalQuery(parameters);
};

// Mask automation via custom toString behavior
const nativeToString = Function.prototype.toString;
Function.prototype.toString = function() {
    if (this === Function.prototype.toString) return nativeToString.call(this);
    const str = nativeToString.call(this);
    if (str.includes('webdriver') || str.includes('automation')) {
        return '() => undefined';
    }
    return str;
};
"""

# ── Context slot ─────────────────────────────────────────────────────────────

@dataclass
class ContextSlot:
    """A reusable browser context with health tracking."""
    context:       Any                    # playwright BrowserContext
    created_at:    float = field(default_factory=time.monotonic)
    used_count:    int   = 0
    last_used_at:  float = field(default_factory=time.monotonic)
    is_healthy:    bool  = True
    max_uses:      int   = 50    # Recycle after N uses (prevents memory leak)

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.created_at

    @property
    def idle_s(self) -> float:
        return time.monotonic() - self.last_used_at

    @property
    def should_recycle(self) -> bool:
        """True if this context should be replaced."""
        return (
            not self.is_healthy
            or self.used_count >= self.max_uses
            or self.age_s > 3600   # Max 1 hour lifetime
        )

    def mark_used(self) -> None:
        self.used_count    += 1
        self.last_used_at   = time.monotonic()


# ── Browser Pool ──────────────────────────────────────────────────────────────

class BrowserPool:
    """
    Persistent browser pool with reusable contexts.

    Architecture:
      - N browser instances (typically 1 for memory-constrained deployments)
      - M contexts per browser (typically 1-2)
      - asyncio.Queue holds available context slots
      - Semaphore enforces maximum concurrent pages

    NEVER call initialize() twice.
    Use get_browser_pool() for the singleton.

    Usage:
        pool = get_browser_pool()
        await pool.initialize()

        async with pool.acquire_page() as page:
            await page.goto(url)
            html = await page.content()
        # Page automatically closed, context returned to pool
    """

    def __init__(
        self,
        max_browsers:   int = 1,
        max_contexts:   int = 1,
        headless:       bool = True,
    ):
        self._max_browsers   = max_browsers
        self._max_contexts   = max_contexts
        self._headless       = headless

        # asyncio.Queue as context pool (thread-safe, async-compatible)
        self._context_queue:  asyncio.Queue[ContextSlot] = asyncio.Queue()
        self._semaphore:      asyncio.Semaphore = asyncio.Semaphore(
            max_browsers * max_contexts
        )

        self._playwright      = None
        self._browsers:       list = []
        self._initialized:    bool = False
        self._init_lock:      asyncio.Lock = asyncio.Lock()

        # Stats
        self._total_pages:    int  = 0
        self._total_recycles: int  = 0
        self._start_time:     float = time.monotonic()

    async def initialize(self) -> None:
        """
        Start browser instances and pre-create contexts.

        Called once at startup. Thread-safe via asyncio.Lock.
        Safe to call multiple times (idempotent).
        """
        async with self._init_lock:
            if self._initialized:
                return

            logger.info(
                f"BrowserPool: initializing "
                f"{self._max_browsers} browser(s) × "
                f"{self._max_contexts} context(s)"
            )
            start = time.monotonic()

            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()

                for _ in range(self._max_browsers):
                    browser = await self._launch_browser()
                    self._browsers.append(browser)

                    for _ in range(self._max_contexts):
                        slot = await self._create_context_slot(browser)
                        await self._context_queue.put(slot)

                elapsed = time.monotonic() - start
                self._initialized = True
                logger.info(
                    f"BrowserPool: ready in {elapsed:.1f}s "
                    f"({self._context_queue.qsize()} contexts available)"
                )

            except Exception as e:
                logger.error(f"BrowserPool: initialization failed: {e}")
                raise

    async def _launch_browser(self) -> Any:
        """Launch a single Chromium browser instance."""
        browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--disable-setuid-sandbox",
                "--disable-features=site-per-process",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-translate",
                "--hide-scrollbars",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-first-run",
                "--safebrowsing-disable-auto-update",
                "--window-size=1440,900",
            ],
        )
        return browser

    async def _create_context_slot(self, browser: Any) -> ContextSlot:
        """Create a new context slot on a browser instance."""
        from scraping.headers import get_random_ua

        # Slight viewport variation (harder to fingerprint)
        viewport_w = random.randint(1280, 1920)
        viewport_h = random.randint(720, 1080)

        ctx = await browser.new_context(
            user_agent=get_random_ua(),
            locale="fr-FR",
            viewport={"width": viewport_w, "height": viewport_h},
            timezone_id="Europe/Paris",
            color_scheme="light",
            device_scale_factor=random.choice([1.0, 1.25, 1.5]),
            extra_http_headers={
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        # Inject stealth script into every page created from this context
        await ctx.add_init_script(_STEALTH_SCRIPT)

        return ContextSlot(context=ctx)

    @asynccontextmanager
    async def acquire_page(
        self,
        timeout_s: int = 30,
    ) -> AsyncGenerator[Any, None]:
        """
        Acquire a Playwright Page from the pool.

        Async context manager that:
          1. Waits for an available context slot (respects semaphore)
          2. Creates a new Page on that context (fast: < 10ms)
          3. Yields the Page to caller
          4. Closes the Page after use
          5. Returns context slot to pool (or recycles if unhealthy)

        Usage:
            async with pool.acquire_page() as page:
                await page.goto(url)
                html = await page.content()

        Raises asyncio.TimeoutError if no context available within timeout_s.
        """
        if not self._initialized:
            await self.initialize()

        async with self._semaphore:
            # Get a context slot (wait up to timeout_s)
            try:
                slot = await asyncio.wait_for(
                    self._context_queue.get(),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"BrowserPool: no context available after {timeout_s}s. "
                    f"All {self._max_contexts} context(s) are busy."
                )

            # Check if slot needs recycling
            if slot.should_recycle:
                logger.info(
                    f"BrowserPool: recycling context "
                    f"(uses={slot.used_count}, age={slot.age_s:.0f}s)"
                )
                try:
                    await slot.context.close()
                except Exception:
                    pass
                # Create fresh context on first available browser
                slot = await self._create_context_slot(self._browsers[0])
                self._total_recycles += 1

            # Create page
            page = None
            try:
                slot.mark_used()
                self._total_pages += 1
                page = await slot.context.new_page()

                # Set route interceptions for performance
                await page.route(
                    "**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,eot}",
                    lambda route: route.abort()
                    if random.random() < 0.7   # Block most images/fonts
                    else route.continue_(),
                )

                yield page

            except Exception as e:
                slot.is_healthy = False
                logger.warning(f"BrowserPool: page error: {e}")
                raise

            finally:
                # Always clean up page and return context to pool
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass
                await self._context_queue.put(slot)

    async def recycle_all(self) -> None:
        """Force recycle all contexts (call after site structure changes)."""
        logger.info("BrowserPool: force recycling all contexts")
        old_slots = []
        while not self._context_queue.empty():
            try:
                slot = self._context_queue.get_nowait()
                old_slots.append(slot)
            except asyncio.QueueEmpty:
                break

        for slot in old_slots:
            try:
                await slot.context.close()
            except Exception:
                pass

        # Create fresh contexts
        for _ in old_slots:
            new_slot = await self._create_context_slot(self._browsers[0])
            await self._context_queue.put(new_slot)

        self._total_recycles += len(old_slots)
        logger.info(f"BrowserPool: recycled {len(old_slots)} contexts")

    async def health_check(self) -> dict:
        """Check pool health and return status."""
        available = self._context_queue.qsize()
        total     = self._max_browsers * self._max_contexts
        uptime    = time.monotonic() - self._start_time

        return {
            "status":         "ok" if self._initialized else "not_initialized",
            "available":      available,
            "total_slots":    total,
            "in_use":         total - available,
            "total_pages":    self._total_pages,
            "total_recycles": self._total_recycles,
            "uptime_s":       round(uptime, 1),
        }

    async def shutdown(self) -> None:
        """Gracefully close all browsers and contexts."""
        logger.info("BrowserPool: shutting down...")

        # Close all context slots
        while not self._context_queue.empty():
            try:
                slot = self._context_queue.get_nowait()
                await slot.context.close()
            except Exception:
                pass

        # Close browsers
        for browser in self._browsers:
            try:
                await browser.close()
            except Exception:
                pass
        self._browsers.clear()

        # Stop Playwright
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

        self._initialized = False
        logger.info("BrowserPool: shutdown complete")


# ── Singleton ─────────────────────────────────────────────────────────────────

_pool: Optional[BrowserPool] = None


def get_browser_pool() -> BrowserPool:
    """
    Return module-level BrowserPool singleton.

    Creates the pool with memory-optimized settings (512MB):
      max_browsers=1, max_contexts=1

    To customize (e.g. higher-memory deployment):
        from scraping.browser.pool import _pool, BrowserPool
        _pool = BrowserPool(max_browsers=1, max_contexts=2)
    """
    global _pool
    if _pool is None:
        import os
        _pool = BrowserPool(
            max_browsers=1,
            max_contexts=int(os.environ.get("MAX_CONCURRENT_PLAYWRIGHT", "1")),
            headless=True,
        )
    return _pool


async def shutdown_browser_pool() -> None:
    """Gracefully shutdown the global pool. Call on app shutdown."""
    global _pool
    if _pool and _pool._initialized:
        await _pool.shutdown()
        _pool = None
