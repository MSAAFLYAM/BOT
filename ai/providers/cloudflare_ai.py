"""
ai/providers/cloudflare_ai.py — Cloudflare Workers AI

Provider مجاني تماماً: 10,000 request/يوم.
يُضاف في cascade AI بعد OpenRouter وقبل Template.

إعداد:
  Environment Variables:
    CF_ACCOUNT_ID = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    CF_API_TOKEN  = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

كيف تحصل عليهما:
  1. dash.cloudflare.com → سجّل مجاناً
  2. My Profile → API Tokens → Create Token
     Template: "Workers AI"
  3. Account ID: يظهر في يمين الصفحة الرئيسية
"""
import logging
import os
from typing import Optional

import httpx

from ai.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger(__name__)

# نماذج مجانية مرتبة من الأفضل للأضعف
CF_MODELS = [
    "@cf/meta/llama-3.1-8b-instruct",
    "@cf/mistral/mistral-7b-instruct-v0.1",
    "@cf/google/gemma-7b-it",
    "@cf/microsoft/phi-2",
]


class CloudflareAIProvider(BaseProvider):
    """
    Cloudflare Workers AI — مجاني 10,000 req/يوم.
    Cascade بين 4 نماذج مجانية.
    """

    name = "cloudflare"

    def __init__(self):
        self._account_id = os.environ.get("CF_ACCOUNT_ID", "")
        self._api_token  = os.environ.get("CF_API_TOKEN", "")

    def is_available(self) -> bool:
        return bool(self._account_id and self._api_token)

    def is_configured(self) -> bool:
        return self.is_available()

    async def generate(
        self,
        prompt:      str,
        max_tokens:  int   = 2000,
        temperature: float = 0.7,
    ) -> ProviderResult:
        """توليد نص عبر Cloudflare Workers AI."""

        if not self.is_configured():
            return ProviderResult(
                success=False,
                error="CF_ACCOUNT_ID أو CF_API_TOKEN غير موجود",
            )

        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type":  "application/json",
        }

        for model in CF_MODELS:
            url = (
                f"https://api.cloudflare.com/client/v4/accounts/"
                f"{self._account_id}/ai/run/{model}"
            )
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(url, headers=headers, json={
                        "messages": [
                            {"role": "system", "content": "Tu es un expert en rédaction SEO."},
                            {"role": "user",   "content": prompt},
                        ],
                        "max_tokens":  max_tokens,
                        "temperature": temperature,
                    })

                    if resp.status_code == 200:
                        data   = resp.json()
                        result = data.get("result", {})
                        text   = result.get("response", "")

                        if text and len(text) > 100:
                            logger.info(f"[cloudflare] ✅ {model} → {len(text)} chars")
                            return ProviderResult(
                                success    = True,
                                text       = text,
                                model      = model,
                                provider   = self.name,
                                latency_ms = 0,
                            )

                    logger.warning(
                        f"[cloudflare] {model} → HTTP {resp.status_code}"
                    )

            except Exception as e:
                logger.warning(f"[cloudflare] {model} → {e}")
                continue

        return ProviderResult(
            success=False,
            error="Tous les modèles Cloudflare ont échoué",
        )
