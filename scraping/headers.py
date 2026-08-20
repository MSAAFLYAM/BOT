"""
scraping/headers.py — Realistic browser header rotation.

Architecture decisions:
  - Headers are organized by browser profile (Chrome, Firefox, Safari).
  - Each profile includes ALL headers a real browser sends.
  - Missing headers (e.g. no Sec-Fetch-*) are a strong bot signal.
  - Platform variants: Windows, macOS, Linux, Android, iOS.
  - Headers are selected deterministically per domain+session to appear
    as the SAME browser across multiple requests (consistency check by sites).
  - Accept-Language matches common real user distributions.

Anti-detection:
  - Real Chrome sends 15+ headers — our pool matches this.
  - Header ORDER matters (some sites fingerprint header order).
  - Sec-Ch-Ua must match User-Agent Chrome version.
  - Accept-Encoding: always include br (Brotli) — real browsers do.
"""
from __future__ import annotations

import hashlib
import random
from typing import Optional

# ── Chrome profiles (most common browser — ~65% market share) ────────────────

_CHROME_WINDOWS_124 = {
    "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language":           "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding":           "gzip, deflate, br, zstd",
    "Cache-Control":             "max-age=0",
    "Sec-Ch-Ua":                 '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile":          "?0",
    "Sec-Ch-Ua-Platform":        '"Windows"',
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT":                       "1",
}

_CHROME_WINDOWS_122 = {
    "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language":           "en-US,en;q=0.9,fr;q=0.8",
    "Accept-Encoding":           "gzip, deflate, br",
    "Cache-Control":             "max-age=0",
    "Sec-Ch-Ua":                 '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile":          "?0",
    "Sec-Ch-Ua-Platform":        '"Windows"',
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "Upgrade-Insecure-Requests": "1",
}

_CHROME_MAC_124 = {
    "User-Agent":                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language":           "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding":           "gzip, deflate, br, zstd",
    "Sec-Ch-Ua":                 '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile":          "?0",
    "Sec-Ch-Ua-Platform":        '"macOS"',
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control":             "max-age=0",
}

_CHROME_ANDROID_124 = {
    "User-Agent":                "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language":           "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding":           "gzip, deflate, br",
    "Sec-Ch-Ua":                 '"Android WebView";v="124", "Not-A.Brand";v="99", "Chromium";v="124"',
    "Sec-Ch-Ua-Mobile":          "?1",
    "Sec-Ch-Ua-Platform":        '"Android"',
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "Upgrade-Insecure-Requests": "1",
    "X-Requested-With":          "com.android.browser",
}

# ── Firefox profiles ───────────────────────────────────────────────────────────

_FIREFOX_WINDOWS_125 = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.8,en-US;q=0.5,en;q=0.3",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "DNT":             "1",
    "Connection":      "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
    "Sec-Fetch-Site":  "none",
    "Sec-Fetch-User":  "?1",
}

_FIREFOX_LINUX_125 = {
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT":             "1",
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
    "Sec-Fetch-Site":  "none",
    "Sec-Fetch-User":  "?1",
}

# ── Safari profiles ────────────────────────────────────────────────────────────

_SAFARI_MAC_17 = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
}

_SAFARI_IOS_17 = {
    "User-Agent":      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# ── All profiles pool ──────────────────────────────────────────────────────────

_ALL_PROFILES = [
    _CHROME_WINDOWS_124,
    _CHROME_WINDOWS_122,
    _CHROME_MAC_124,
    _CHROME_ANDROID_124,
    _FIREFOX_WINDOWS_125,
    _FIREFOX_LINUX_125,
    _SAFARI_MAC_17,
    _SAFARI_IOS_17,
]

# Desktop-only profiles (preferred for Amazon — mobile may get different content)
_DESKTOP_PROFILES = [
    _CHROME_WINDOWS_124,
    _CHROME_WINDOWS_122,
    _CHROME_MAC_124,
    _FIREFOX_WINDOWS_125,
    _FIREFOX_LINUX_125,
    _SAFARI_MAC_17,
]

# ── curl-cffi impersonation targets ───────────────────────────────────────────

CURL_CFFI_PROFILES = [
    "chrome124",
    "chrome120",
    "chrome116",
    "chrome110",
    "firefox121",
    "safari17_0",
    "safari15_5",
]


def get_headers(
    url: str = "",
    desktop_only: bool = True,
    session_seed: Optional[str] = None,
) -> dict[str, str]:
    """
    Get a realistic browser header set.

    Args:
        url:          Target URL (used for Referer construction).
        desktop_only: If True, only desktop browser profiles.
        session_seed: If provided, selects headers deterministically.
                      Use the domain as seed to get consistent headers
                      per domain across requests (looks more like a real user).

    Returns:
        Dict of HTTP headers.

    Usage:
        headers = get_headers("https://www.amazon.fr/dp/B08XYZ123")
        headers["Referer"] = "https://www.amazon.fr/"
    """
    pool = _DESKTOP_PROFILES if desktop_only else _ALL_PROFILES

    if session_seed:
        # Deterministic selection: same seed → same profile
        idx = int(hashlib.md5(session_seed.encode()).hexdigest()[:8], 16) % len(pool)
        profile = dict(pool[idx])
    else:
        profile = dict(random.choice(pool))

    # Add Referer for non-root requests
    if url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            referer = f"{parsed.scheme}://{parsed.netloc}/"
            profile["Referer"] = referer
        except Exception:
            pass

    return profile


def get_random_ua() -> str:
    """Return a random User-Agent string from the desktop pool."""
    return random.choice(_DESKTOP_PROFILES)["User-Agent"]


def get_curl_cffi_profile(session_seed: Optional[str] = None) -> str:
    """
    Return a curl-cffi impersonation profile name.

    Uses session_seed for deterministic selection if provided.
    """
    if session_seed:
        idx = int(hashlib.md5(session_seed.encode()).hexdigest()[:4], 16) % len(CURL_CFFI_PROFILES)
        return CURL_CFFI_PROFILES[idx]
    return random.choice(CURL_CFFI_PROFILES)
