"""
scraping/fetch/engine.py — 6-Layer Hybrid Fetch Engine.

Layers (in execution order):
  1. CACHE    → Redis HTML cache (0ms, highest priority)
  2. CURL     → curl-cffi TLS impersonation (50-300ms, 4 profiles)
  3. HTTPX    → async HTTP/2 client (100-500ms)
  4. AIOHTTP  → async alternative (100-500ms, different TLS)
  5. BROWSER  → Playwright from persistent pool (3-30s, last resort)
  6. SNAPSHOT → On failure, save HTML snapshot for debugging

Layer selection is intelligent:
  - Blocked responses (403, Cloudflare) → skip remaining light layers → jump to Browser
  - Timeout → retry same layer (network issue, not block)
  - Parse failure → save snapshot → mark as error
  - 404 → permanent stop, no retry

Architecture decisions:
  - FetchResult replaces ScrapeResult (richer context).
  - Each layer reports: status_code, latency, cookies, method name.
  - Cookies from each successful request are saved to Redis (session persistence).
  - Rate limiter consulted before each domain request.
  - Metrics recorded after every attempt (success or failure).
  - Browser layer uses persistent pool (NEVER creates new browser per request).
  - HTML cache is checked FIRST, WRITTEN after any successful fetch.

Memory:
  - HTTPX client is stateless (no persistent state).
  - aiohttp session is created per fetch (not pooled — safer under concurrency).
  - curl-cffi runs in executor (sync library, non-blocking with run_in_executor).
   - Playwright uses BrowserPool singleton (1 browser, 1 context).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from scraping.headers import get_headers, get_curl_cffi_profile, CURL_CFFI_PROFILES

logger = logging.getLogger(__name__)


# ── Fetch Result ──────────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    """
    Complete result from any fetch layer.

    Carries enough context for:
      - Metrics recording (method, latency, status)
      - Retry decisions (status_code, is_blocked, error_type)
      - Cookie persistence (cookies)
      - Cache storage (html)
      - Debugging (error, snapshot_key)
    """
    url:            str
    html:           str              = ""
    status_code:    int              = 0
    method:         str              = ""
    latency_ms:     float            = 0.0
    cookies:        dict             = field(default_factory=dict)
    from_cache:     bool             = False
    snapshot_key:   str              = ""
    error:          Optional[str]    = None
    error_type:     str              = ""    # "blocked" | "timeout" | "network" | "parse"
    attempts:       int              = 0     # How many layers tried

    @property
    def success(self) -> bool:
        return bool(self.html) and self.status_code == 200

    @property
    def is_blocked(self) -> bool:
        """True if response content indicates bot detection."""
        return _detect_block(self.html, self.status_code)

    @property
    def domain(self) -> str:
        try:
            return urlparse(self.url).netloc.lower()
        except Exception:
            return "unknown"


# ── Block detection ───────────────────────────────────────────────────────────

_BLOCK_SIGNALS = frozenset([
    "cf-browser-verification",
    "cloudflare",
    "challenge-form",
    "captcha",
    "please enable javascript",
    "enable javascript and cookies",
    "access denied",
    "robot ou humain",
    "are you a human",
    "ddos protection",
    "ray id",
    "checking your browser",
    "please wait while we redirect",
    "i am not a robot",
    "amazon.com/errors/validatecaptcha",
    "enter the characters you see below",
    "sorry, we just need to make sure",
    "press & hold to confirm",
    "verifying you are human",
])


def _detect_block(html: str, status_code: int) -> bool:
    """
    Detect bot-detection / Cloudflare challenge pages.

    A 200 with Cloudflare HTML is treated as a block.
    A short response (< 500 chars) is almost certainly a block redirect.
    """
    if status_code in (403, 429):
        return True
    if not html or len(html) < 300:
        return True
    lower = html.lower()
    return any(sig in lower for sig in _BLOCK_SIGNALS)


# ── Individual Layer Fetchers ─────────────────────────────────────────────────

async def _fetch_with_curl(
    url:     str,
    headers: dict,
    cookies: dict,
    profile: str,
    timeout: int,
) -> FetchResult:
    """
    curl-cffi layer — TLS fingerprint impersonation.

    Runs synchronous curl-cffi in an executor (non-blocking).
    TLS fingerprint matches real Chrome/Firefox/Safari.
    This defeats JA3/JA4 TLS fingerprint detection.
    """
    start = time.monotonic()

    def _sync() -> tuple[int, str, dict]:
        try:
            from curl_cffi import requests as cr
            session = cr.Session(impersonate=profile)
            resp = session.get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=timeout,
                allow_redirects=True,
            )
            return resp.status_code, resp.text, dict(resp.cookies)
        except Exception as e:
            return 0, "", {}

    try:
        loop = asyncio.get_event_loop()
        status, html, resp_cookies = await loop.run_in_executor(None, _sync)
        latency = (time.monotonic() - start) * 1000
        return FetchResult(
            url=url,
            html=html if status == 200 else "",
            status_code=status,
            method=f"curl-cffi/{profile}",
            latency_ms=latency,
            cookies=resp_cookies,
        )
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        return FetchResult(url=url, method=f"curl-cffi/{profile}",
                           latency_ms=latency, error=str(e)[:200], error_type="network")


async def _fetch_with_httpx(
    url:     str,
    headers: dict,
    cookies: dict,
    timeout: int,
) -> FetchResult:
    """
    HTTPX async layer — HTTP/2 support.

    HTTP/2 matches modern browser behavior more closely.
    Different from curl-cffi TLS profile → provides variety.
    """
    start = time.monotonic()
    try:
        import httpx
        async with httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=10.0, pool=5.0),
            headers=headers,
            cookies=cookies,
            verify=True,
        ) as client:
            resp = await client.get(url)
            latency = (time.monotonic() - start) * 1000
            return FetchResult(
                url=url,
                html=resp.text if resp.status_code == 200 else "",
                status_code=resp.status_code,
                method="httpx",
                latency_ms=latency,
                cookies=dict(resp.cookies),
            )
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        err_type = "timeout" if "timeout" in str(e).lower() else "network"
        return FetchResult(url=url, method="httpx", latency_ms=latency,
                           error=str(e)[:200], error_type=err_type)


async def _fetch_with_aiohttp(
    url:     str,
    headers: dict,
    cookies: dict,
    timeout: int,
) -> FetchResult:
    """
    aiohttp async layer — alternative TLS fingerprint.

    Different from both httpx and curl-cffi.
    Good for sites that block specific TLS profiles.
    """
    start = time.monotonic()
    try:
        import aiohttp
        timeout_cfg = aiohttp.ClientTimeout(total=timeout, connect=10)
        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout_cfg,
        ) as session:
            async with session.get(
                url,
                cookies=cookies,
                allow_redirects=True,
                ssl=True,
            ) as resp:
                html    = await resp.text(errors="replace")
                latency = (time.monotonic() - start) * 1000
                resp_cookies = {k: v.value for k, v in resp.cookies.items()}
                return FetchResult(
                    url=url,
                    html=html if resp.status == 200 else "",
                    status_code=resp.status,
                    method="aiohttp",
                    latency_ms=latency,
                    cookies=resp_cookies,
                )
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        err_type = "timeout" if "timeout" in str(e).lower() else "network"
        return FetchResult(url=url, method="aiohttp", latency_ms=latency,
                           error=str(e)[:200], error_type=err_type)


async def _fetch_with_playwright(
    url:     str,
    headers: dict,
    cookies: dict,
    timeout: int,
) -> FetchResult:
    """
    Playwright layer — JavaScript rendering via persistent browser pool.

    Uses BrowserPool singleton — NEVER creates a new browser.
    Page is created and destroyed per request (< 10ms overhead).
    Context is reused (eliminates 3-5s browser startup per request).
    """
    start = time.monotonic()
    try:
        from scraping.browser.pool import get_browser_pool

        pool = get_browser_pool()

        async with pool.acquire_page(timeout_s=15) as page:
            # Set extra headers
            await page.set_extra_http_headers({
                k: v for k, v in headers.items()
                if k not in ("User-Agent",)
            })

            # Set cookies if available
            if cookies:
                parsed = urlparse(url)
                pw_cookies = [
                    {
                        "name":   k,
                        "value":  v,
                        "domain": parsed.netloc,
                        "path":   "/",
                    }
                    for k, v in cookies.items()
                ]
                await page.context.add_cookies(pw_cookies)

            # Navigate with network idle wait
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout * 1000,
            )
            status = response.status if response else 200

            # Wait for main content
            try:
                await page.wait_for_selector(
                    "#productTitle, h1, main, article, [class*='product']",
                    timeout=8000,
                )
            except Exception:
                pass  # Continue even if selector not found

            # Human-like pause
            await asyncio.sleep(random.uniform(0.8, 2.0))

            html = await page.content()

            # Collect cookies
            ctx_cookies = await page.context.cookies()
            resp_cookies = {c["name"]: c["value"] for c in ctx_cookies}

            latency = (time.monotonic() - start) * 1000

            return FetchResult(
                url=url,
                html=html if html and len(html) > 500 else "",
                status_code=status,
                method="playwright",
                latency_ms=latency,
                cookies=resp_cookies,
            )

    except asyncio.TimeoutError:
        latency = (time.monotonic() - start) * 1000
        return FetchResult(url=url, method="playwright", latency_ms=latency,
                           error="Playwright timeout", error_type="timeout")
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        logger.warning(f"Playwright error for {url[:60]}: {e}")
        return FetchResult(url=url, method="playwright", latency_ms=latency,
                           error=str(e)[:200], error_type="network")


# ── Hybrid Fetch Engine ────────────────────────────────────────────────────────

class HybridFetchEngine:
    """
    7-layer hybrid fetch engine.

    Layer execution order:
      0. Jina AI Reader — FREE, ultra-fast, clean Markdown (NEW)
      1. Cache (Redis) — always checked first
      2. curl-cffi (4 TLS profiles) — lightweight, anti-fingerprint
      3. HTTPX (HTTP/2) — lightweight async
      4. aiohttp — lightweight async (different TLS)
      5. Playwright (pool) — browser, last resort

    Jina (Layer 0) resolves 80% of URLs in <2s with no RAM overhead.
    Playwright is only used when all other layers fail.

    After each layer:
      - Success → cache result, save cookies, return
      - Blocked → skip remaining light layers, jump to Playwright
      - Timeout → retry next light layer (not blocked, just slow)
      - 404 → stop immediately

    Usage:
        engine = HybridFetchEngine()
        result = await engine.fetch("https://www.amazon.fr/dp/B08XYZ123")
        if result.success and not result.is_blocked:
            html = result.html
    """

    def __init__(
        self,
        content_type:       str  = "product",
        enable_playwright:  bool = True,
        skip_cache:         bool = False,
    ):
        self.content_type      = content_type
        self.enable_playwright = enable_playwright
        self.skip_cache        = skip_cache

        from scraping.cache.html_cache   import get_html_cache
        from scraping.fetch.rate_limiter import get_rate_limiter
        from scraping.metrics            import get_metrics

        self._cache       = get_html_cache()
        self._rate        = get_rate_limiter()
        self._metrics     = get_metrics()

    async def fetch(self, url: str) -> FetchResult:
        """
        Fetch a URL through the 6-layer cascade.

        Returns FetchResult. Check result.success before using result.html.
        """
        domain = urlparse(url).netloc.lower()

        # ── Layer 0: Jina AI Reader (FREE, ultra-fast) ────────────────────
        # يعمل على المواقع الإخبارية والمدونات — لا يعمل على Amazon
        _is_article_url = not any(x in url for x in [
            "amazon.", "amzn.", "/dp/", "/gp/product"
        ])
        if _is_article_url:
            try:
                from scraping.fetch.jina_reader import fetch_with_jina
                jina_content = await fetch_with_jina(url)
                if jina_content:
                    logger.info(f"⚡ Jina: {url[:60]}")
                    self._metrics.record_fetch_success(domain, "jina", 0)
                    result = FetchResult(
                        url=url, html=jina_content,
                        status_code=200, method="jina",
                    )
                    return await self._on_success(result, domain, url)
            except Exception as _e:
                logger.debug(f"[jina] skip: {_e}")

        # ── Layer 1: Cache ─────────────────────────────────────────────────
        if not self.skip_cache:
            cache_result = await self._cache.get(url)
            if cache_result.hit and not _detect_block(cache_result.html, 200):
                self._metrics.record_fetch_success(domain, "cache", 0, from_cache=True)
                logger.info(f"⚡ Cache: {url[:60]}")
                return FetchResult(
                    url=url, html=cache_result.html,
                    status_code=200, method="cache",
                    from_cache=True,
                )
            self._metrics.record_cache_miss(domain)

        # ── Rate limit ─────────────────────────────────────────────────────
        await self._rate.wait(domain)

        # ── Prepare headers and cookies ────────────────────────────────────
        headers = get_headers(url, desktop_only=True, session_seed=domain)
        cookies = await self._load_cookies(domain)

        # ── Layers 2-4: Light layers ───────────────────────────────────────
        light_layers = self._build_light_layers(url, headers, cookies)
        last_result  = FetchResult(url=url, error="All methods exhausted")
        block_count  = 0

        for layer_name, layer_coro in light_layers:
            last_result = await layer_coro()
            last_result.attempts += 1

            if last_result.success and not last_result.is_blocked:
                return await self._on_success(last_result, domain, url)

            if last_result.is_blocked:
                block_count += 1
                self._metrics.record_fetch_blocked(
                    domain, layer_name,
                    last_result.status_code,
                    last_result.latency_ms,
                )
                self._rate.record_outcome(domain, "block")
                logger.warning(
                    f"❌ {layer_name}: blocked ({last_result.status_code})"
                    f" {url[:50]}"
                )
                # If majority of light layers blocked → go to Playwright immediately
                if block_count >= 2:
                    logger.info("Multiple blocks detected → jumping to Playwright")
                    break
            elif last_result.error_type == "timeout":
                self._rate.record_outcome(domain, "timeout")
                self._metrics.record_fetch_failed(
                    domain, layer_name, last_result.error or "", last_result.latency_ms
                )
            else:
                self._metrics.record_fetch_failed(
                    domain, layer_name, last_result.error or "", last_result.latency_ms
                )
                self._rate.record_outcome(domain, "error")

            # 404: stop immediately
            if last_result.status_code == 404:
                logger.warning(f"404 for {url[:60]} — permanent stop")
                return last_result

        # ── Layer 5: Playwright (browser fallback) ─────────────────────────
        if self.enable_playwright:
            self._metrics.record_browser_fallback(domain)
            logger.info(f"🌐 Browser fallback: {url[:60]}")

            result = await _fetch_with_playwright(url, headers, cookies, timeout=45)
            result.attempts = last_result.attempts + 1

            if result.success and not result.is_blocked:
                return await self._on_success(result, domain, url)

            if result.is_blocked:
                self._metrics.record_fetch_blocked(
                    domain, "playwright", result.status_code, result.latency_ms
                )
                self._rate.record_outcome(domain, "block")
            else:
                self._rate.record_outcome(domain, "error")

            last_result = result

        # ── Layer 6: Snapshot (all layers failed) ──────────────────────────
        if last_result.html:
            snapshot_key = await self._cache.save_snapshot(
                url, last_result.html,
                reason=f"all_methods_failed/{last_result.status_code}"
            )
            last_result.snapshot_key = snapshot_key

        logger.error(
            json.dumps({
                "event":      "all_methods_failed",
                "url":        url[:80],
                "domain":     domain,
                "attempts":   last_result.attempts,
                "last_status": last_result.status_code,
            })
        )
        return last_result

    def _build_light_layers(
        self,
        url:     str,
        headers: dict,
        cookies: dict,
    ) -> list[tuple[str, callable]]:
        """
        Build ordered list of lightweight fetch layers.

        curl-cffi profiles tried in rotation for TLS variety.
        """
        timeout = 25
        layers  = []

        # curl-cffi: 4 profiles
        for profile in CURL_CFFI_PROFILES[:4]:
            p = profile  # capture
            layers.append((
                f"curl-cffi/{p}",
                lambda _p=p: _fetch_with_curl(url, headers, cookies, _p, timeout),
            ))

        # HTTPX
        layers.append((
            "httpx",
            lambda: _fetch_with_httpx(url, headers, cookies, timeout),
        ))

        # aiohttp
        layers.append((
            "aiohttp",
            lambda: _fetch_with_aiohttp(url, headers, cookies, timeout),
        ))

        return layers

    async def _on_success(
        self,
        result: FetchResult,
        domain: str,
        url:    str,
    ) -> FetchResult:
        """Handle successful fetch: cache, cookies, metrics."""
        # Cache the HTML
        await self._cache.set(url, result.html, content_type=self.content_type)

        # Persist cookies
        if result.cookies:
            from core.redis_client import store_session
            await store_session(domain, result.cookies)

        # Record metrics
        self._metrics.record_fetch_success(domain, result.method, result.latency_ms)
        self._rate.record_outcome(domain, "ok")

        logger.info(
            json.dumps({
                "event":      "fetch_success",
                "method":     result.method,
                "domain":     domain,
                "latency_ms": round(result.latency_ms, 1),
                "html_kb":    round(len(result.html) / 1024, 1),
                "from_cache": result.from_cache,
            })
        )
        return result

    async def _load_cookies(self, domain: str) -> dict:
        """Load persisted cookies for domain from Redis."""
        try:
            from core.redis_client import get_session
            cookies = await get_session(domain)
            return cookies or {}
        except Exception:
            return {}


# ── Module-level convenience ───────────────────────────────────────────────────

async def fetch_url(
    url:               str,
    content_type:      str  = "product",
    enable_playwright: bool = True,
    skip_cache:        bool = False,
) -> FetchResult:
    """
    Convenience function: create engine and fetch URL.

    Usage:
        result = await fetch_url("https://www.amazon.fr/dp/B08XYZ123")
        if result.success:
            html = result.html
    """
    engine = HybridFetchEngine(
        content_type=content_type,
        enable_playwright=enable_playwright,
        skip_cache=skip_cache,
    )
    return await engine.fetch(url)
