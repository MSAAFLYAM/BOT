"""
ai/providers/litellm_provider.py — LiteLLM Unified Provider

واجهة واحدة تدعم 100+ نموذج AI.
بدلاً من 4 ملفات providers منفصلة — استدعاء واحد يعمل مع الجميع.

التثبيت:
    pip install litellm

النماذج المجانية المدعومة:
    groq/llama-3.1-70b-versatile
    gemini/gemini-1.5-flash
    cloudflare/@cf/meta/llama-3.1-8b-instruct
    openrouter/meta-llama/llama-3.1-8b-instruct:free

Environment Variables:
    GROQ_API_KEY     = gsk_...
    GEMINI_API_KEY   = AIza...
    CF_API_TOKEN     = ...     (Cloudflare)
    CF_ACCOUNT_ID    = ...     (Cloudflare)
    OPENROUTER_API_KEY = sk-or-...
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from ai.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger(__name__)

# Cascade من الأفضل للأضعف — كلها مجانية
LITELLM_MODELS = [
    # Groq — الأسرع والأجود مجاناً
    "groq/llama-3.1-70b-versatile",
    "groq/llama-3.1-8b-instant",

    # Gemini — جيد للمقالات الطويلة
    "gemini/gemini-1.5-flash",
    "gemini/gemini-1.5-flash-8b",

    # Cloudflare — مجاني 10,000 req/يوم
    "cloudflare/@cf/meta/llama-3.1-8b-instruct",
    "cloudflare/@cf/mistral/mistral-7b-instruct-v0.1",

    # OpenRouter — free tier
    "openrouter/meta-llama/llama-3.1-8b-instruct:free",
    "openrouter/mistralai/mistral-7b-instruct:free",
]


def _set_litellm_keys() -> None:
    """تعيين API keys لـ LiteLLM من environment variables."""
    import litellm

    # Groq
    if key := os.environ.get("GROQ_API_KEY"):
        os.environ["GROQ_API_KEY"] = key

    # Gemini
    if key := os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = key

    # Cloudflare
    if cf_id := os.environ.get("CF_ACCOUNT_ID"):
        if cf_tok := os.environ.get("CF_API_TOKEN"):
            os.environ["CLOUDFLARE_API_KEY"] = cf_tok
            os.environ["CLOUDFLARE_ACCOUNT_ID"] = cf_id

    # OpenRouter
    if key := os.environ.get("OPENROUTER_API_KEY"):
        os.environ["OPENROUTER_API_KEY"] = key

    # Silence non-critical warnings
    litellm.suppress_debug_info = True
    litellm.drop_params          = True


class LiteLLMProvider(BaseProvider):
    """
    LiteLLM Unified Provider.

    يجرب كل النماذج المجانية تلقائياً.
    إذا فشل أي نموذج → ينتقل للتالي تلقائياً.
    """
    name = "litellm"

    def __init__(self):
        try:
            _set_litellm_keys()
            self._available = True
        except ImportError:
            self._available = False
            logger.warning("[litellm] litellm غير مثبت — pip install litellm")

    def is_available(self) -> bool:
        return self._available

    async def generate(
        self,
        prompt:      str,
        max_tokens:  int   = 2000,
        temperature: float = 0.7,
    ) -> ProviderResult:

        if not self._available:
            return ProviderResult(
                success=False,
                error="litellm غير مثبت"
            )

        start = time.monotonic()

        for model in LITELLM_MODELS:
            try:
                result = await self._call_model(
                    model, prompt, max_tokens, temperature
                )
                if result.success:
                    result.latency_ms = (time.monotonic() - start) * 1000
                    logger.info(
                        f"[litellm] ✅ {model} → "
                        f"{len(result.text)} chars, "
                        f"{result.latency_ms:.0f}ms"
                    )
                    return result

            except Exception as e:
                err = str(e).lower()
                # Rate limit → انتظر وجرب التالي
                if "rate" in err or "429" in err:
                    logger.warning(f"[litellm] {model} rate limited")
                    import asyncio
                    await asyncio.sleep(1)
                # Auth error → جرب التالي فوراً
                elif "auth" in err or "key" in err or "401" in err:
                    logger.debug(f"[litellm] {model} auth error")
                else:
                    logger.debug(f"[litellm] {model} failed: {e}")
                continue

        return ProviderResult(
            success=False,
            error="Tous les modèles LiteLLM ont échoué"
        )

    async def _call_model(
        self,
        model:       str,
        prompt:      str,
        max_tokens:  int,
        temperature: float,
    ) -> ProviderResult:
        """Appel async via LiteLLM."""
        import asyncio
        from litellm import acompletion

        response = await asyncio.wait_for(
            acompletion(
                model=model,
                messages=[
                    {
                        "role":    "system",
                        "content": "Tu es un expert en rédaction SEO francophone."
                    },
                    {
                        "role":    "user",
                        "content": prompt
                    },
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            ),
            timeout=45,
        )

        text = response.choices[0].message.content or ""

        if not text or len(text) < 100:
            return ProviderResult(success=False, error="Réponse trop courte")

        return ProviderResult(
            success  = True,
            text     = text,
            model    = model,
            provider = self.name,
            tokens   = getattr(response.usage, "total_tokens", 0),
        )
