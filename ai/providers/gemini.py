"""
ai/providers/gemini.py — Google Gemini provider.

Google Gemini free tier:
  - Gemini 1.5 Flash: 15 RPM, 1M tokens/day — FREE
  - Gemini 1.5 Pro:   2 RPM, 50 req/day — FREE (limited)
  - Gemini 2.0 Flash: 15 RPM — FREE

Get API key: https://aistudio.google.com (free account)
Add to .env: GEMINI_API_KEY = AIzaSy...

Install: pip install google-generativeai
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from ai.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-1.5-flash",       # Best free: 15 RPM, 1M tokens/day
    "gemini-1.5-flash-8b",    # Faster, lighter
    "gemini-2.0-flash-exp",   # Latest experimental (free)
    "gemini-1.5-pro",         # Higher quality but limited
]


class GeminiProvider(BaseProvider):
    """
    Google Gemini provider — free tier.

    Uses google-generativeai SDK (sync, run in executor).
    Auto-selects best available model.
    """
    name  = "gemini"
    model = "gemini-1.5-flash"

    def __init__(self, model: Optional[str] = None):
        self._api_key = os.environ.get("GEMINI_API_KEY", "")
        if model:
            self.model = model

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def generate(
        self,
        prompt:     str,
        max_tokens: int   = 2000,
        temperature:float = 0.7,
    ) -> ProviderResult:
        if not self.is_available():
            return ProviderResult("", self.name, self.model, success=False,
                                  error="GEMINI_API_KEY not configured")

        start = time.monotonic()
        for model in [self.model] + [m for m in GEMINI_MODELS if m != self.model]:
            try:
                text, tokens = await self._call(prompt, model, max_tokens, temperature)
                latency = (time.monotonic() - start) * 1000
                logger.info(f"[gemini] ✅ {model} — {tokens} tokens, {latency:.0f}ms")
                return ProviderResult(
                    text=text, provider=self.name, model=model,
                    tokens=tokens, latency_ms=latency, success=True,
                )
            except Exception as e:
                err = str(e)
                if "quota" in err.lower() or "429" in err or "Resource exhausted" in err:
                    logger.warning(f"[gemini] {model} quota exceeded, trying next...")
                    await asyncio.sleep(2)
                    continue
                elif "404" in err or "not found" in err.lower():
                    logger.warning(f"[gemini] {model} not available, trying next...")
                    continue
                logger.warning(f"[gemini] {model} error: {err[:100]}")
                continue

        latency = (time.monotonic() - start) * 1000
        return ProviderResult("", self.name, self.model, success=False,
                              error="All Gemini models failed/quota exceeded",
                              latency_ms=latency)

    async def _call(
        self,
        prompt:     str,
        model:      str,
        max_tokens: int,
        temperature:float,
    ) -> tuple[str, int]:
        """Run Gemini API call in executor (sync SDK)."""
        def _sync():
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)

            gen_config = {
                "max_output_tokens": max_tokens,
                "temperature":       temperature,
            }
            m    = genai.GenerativeModel(model, generation_config=gen_config)
            resp = m.generate_content(prompt)
            text = resp.text or ""
            try:
                tokens = resp.usage_metadata.total_token_count
            except Exception:
                tokens = len(text.split()) * 1.3
            return text, int(tokens)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)
