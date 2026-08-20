"""
ai/providers/groq.py — Groq API provider.

Groq is FREE with generous limits:
  - 14,400 requests/day per key
  - 3 keys = 43,200 requests/day (GROQ_API_KEY + GROQ_API_KEY_2 + GROQ_API_KEY_3)
  - 100 requests/minute
  - Models: Llama 3.1 70B, Llama 3.1 8B, Mixtral 8x7B, Gemma 2 9B

Groq uses LPU (Language Processing Unit) → 10x faster than GPU.
Llama 3.1 70B on Groq = quality comparable to GPT-4o-mini, FREE.

Get API key: https://console.groq.com (free account)
Add to .env:
  GROQ_API_KEY   = gsk_...   (account 1)
  GROQ_API_KEY_2 = gsk_...   (account 2 — optional)
  GROQ_API_KEY_3 = gsk_...   (account 3 — optional)

Install: pip install groq
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from ai.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger(__name__)

# Best models on Groq (free)
GROQ_MODELS = [
    "llama-3.3-70b-versatile",   # Best quality 2025, free
    "llama-3.1-8b-instant",      # Fast, free
    "llama3-70b-8192",           # Stable fallback
    "gemma2-9b-it",              # Google Gemma, free
    "mixtral-8x7b-32768",        # Long context
]


class GroqProvider(BaseProvider):
    """
    Groq AI provider — free, ultra-fast.

    Uses official groq Python SDK.
    Falls back to next model if rate limited.
    """
    name  = "groq"
    model = "llama-3.3-70b-versatile"

    def __init__(self, model: Optional[str] = None):
        # ── Key Rotation: 3 comptes Groq = 43,200 req/jour gratuits ──────────
        _all_keys = [
            os.environ.get("GROQ_API_KEY",   ""),
            os.environ.get("GROQ_API_KEY_2", ""),
            os.environ.get("GROQ_API_KEY_3", ""),
        ]
        self._keys = [k.strip() for k in _all_keys if k.strip()]
        self._key_index = 0
        if model:
            self.model = model

    def _next_key(self) -> str:
        """Rotation circulaire entre les clés disponibles."""
        if not self._keys:
            return ""
        key = self._keys[self._key_index % len(self._keys)]
        self._key_index += 1
        return key

    @property
    def _api_key(self) -> str:
        return self._keys[0] if self._keys else ""

    def is_available(self) -> bool:
        return bool(self._keys)

    async def generate(
        self,
        prompt:     str,
        max_tokens: int   = 2000,
        temperature:float = 0.7,
    ) -> ProviderResult:
        if not self.is_available():
            return ProviderResult("", self.name, self.model, success=False,
                                  error="GROQ_API_KEY not configured")

        start = time.monotonic()
        try:
            # Try each Groq model in order
            for model in [self.model] + [m for m in GROQ_MODELS if m != self.model]:
                try:
                    result = await self._call(prompt, model, max_tokens, temperature)
                    result.latency_ms = (time.monotonic() - start) * 1000
                    logger.info(
                        f"[groq] ✅ {model} — "
                        f"{result.tokens} tokens, {result.latency_ms:.0f}ms"
                    )
                    return result
                except Exception as e:
                    err = str(e)
                    if "rate_limit" in err.lower() or "429" in err:
                        logger.warning(f"[groq] {model} rate limited, trying next...")
                        import asyncio; await asyncio.sleep(2)
                        continue
                    raise

            return ProviderResult("", self.name, self.model, success=False,
                                  error="All Groq models rate limited")

        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            logger.warning(f"[groq] Failed: {e}")
            return ProviderResult("", self.name, self.model, success=False,
                                  error=str(e)[:200], latency_ms=latency)

    async def _call(
        self,
        prompt:     str,
        model:      str,
        max_tokens: int,
        temperature:float,
    ) -> ProviderResult:
        """Make actual API call using groq SDK via executor (sync SDK)."""
        import asyncio

        # اختر المفتاح التالي في الـ rotation
        current_key = self._next_key()

        def _sync_call():
            from groq import Groq
            client = Groq(api_key=current_key)
            resp   = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text   = resp.choices[0].message.content or ""
            tokens = getattr(resp.usage, 'total_tokens', 0)
            return text, tokens

        loop           = asyncio.get_event_loop()
        text, tokens   = await loop.run_in_executor(None, _sync_call)

        if not text or len(text) < 50:
            raise ValueError(f"Groq returned empty response (model={model})")

        return ProviderResult(
            text=text, provider=self.name, model=model,
            tokens=tokens, success=True,
        )
