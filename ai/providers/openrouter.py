"""
ai/providers/openrouter.py — OpenRouter provider (refactored).

Tries paid models first, then cascades through all free models.
Handles 402 (no credits) and 404 (model not found) gracefully.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from ai.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger(__name__)

PAID_MODELS = [
    "openai/gpt-4o-mini",
    "anthropic/claude-haiku-4-5",
    "google/gemini-flash-1.5",
]

FREE_MODELS = [
    "meta-llama/llama-3.1-8b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "google/gemma-2-9b-it:free",
    "qwen/qwen-2-7b-instruct:free",
    "microsoft/phi-3-mini-128k-instruct:free",
    "deepseek/deepseek-r1-0528:free",
    "deepseek/deepseek-chat-v3-0324:free",
]


class OpenRouterProvider(BaseProvider):
    """OpenRouter provider with paid → free cascade."""

    name  = "openrouter"
    model = "openai/gpt-4o-mini"

    def __init__(self, free_only: bool = False):
        self._api_key  = os.environ.get("OPENROUTER_API_KEY", "")
        self._free_only= free_only

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
                                  error="OPENROUTER_API_KEY not configured")

        configured = os.environ.get("OPENROUTER_MODEL", "")
        models_to_try = []

        if not self._free_only:
            if configured:
                models_to_try.append(configured)
            models_to_try.extend(PAID_MODELS)

        models_to_try.extend(FREE_MODELS)

        start = time.monotonic()
        for model in models_to_try:
            try:
                text, tokens = await self._call(prompt, model, max_tokens, temperature)
                latency = (time.monotonic() - start) * 1000
                logger.info(f"[openrouter] ✅ {model} — {latency:.0f}ms")
                return ProviderResult(
                    text=text, provider=self.name, model=model,
                    tokens=tokens, latency_ms=latency, success=True,
                )
            except Exception as e:
                err = str(e)
                if "402" in err or "credits" in err.lower():
                    logger.warning(f"[openrouter] {model}: no credits, trying next")
                    continue
                elif "404" in err or "No endpoints" in err:
                    logger.warning(f"[openrouter] {model}: not found, trying next")
                    continue
                elif "429" in err:
                    logger.warning(f"[openrouter] {model}: rate limited, waiting...")
                    await asyncio.sleep(5)
                    continue
                logger.warning(f"[openrouter] {model}: {err[:80]}")
                continue

        return ProviderResult("", self.name, self.model, success=False,
                              error="All OpenRouter models failed")

    async def _call(
        self,
        prompt:     str,
        model:      str,
        max_tokens: int,
        temperature:float,
    ) -> tuple[str, int]:
        import httpx
        resp = await asyncio.wait_for(
            self._post(prompt, model, max_tokens, temperature),
            timeout=90,
        )
        if resp.status_code == 402:
            raise ValueError(f"402: no credits for {model}")
        if resp.status_code == 404:
            raise ValueError(f"404: model {model} not found")
        if resp.status_code != 200:
            raise ValueError(f"{resp.status_code}: {resp.text[:100]}")
        data    = resp.json()
        text    = data["choices"][0]["message"]["content"] or ""
        tokens  = data.get("usage", {}).get("total_tokens", 0)
        if len(text) < 50:
            raise ValueError(f"Empty response from {model}")
        return text, tokens

    async def _post(self, prompt, model, max_tokens, temperature):
        import httpx
        async with httpx.AsyncClient(timeout=90) as client:
            return await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type":  "application/json",
                    "HTTP-Referer":  "https://amazon-bot-pin.example.com",
                },
                json={
                    "model":       model,
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  max_tokens,
                    "temperature": temperature,
                },
            )
