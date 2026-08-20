"""
ai/providers/base.py — Abstract base for all AI providers.

Each provider implements:
  - is_available() → bool: is API key configured?
  - generate(prompt, max_tokens) → str: generate text
  - health_check() → dict: verify API works

Providers are tried in order by HybridAIGenerator.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ProviderResult:
    text:       str
    provider:   str
    model:      str
    tokens:     int   = 0
    latency_ms: float = 0.0
    success:    bool  = True
    error:      Optional[str] = None


class BaseProvider(ABC):
    """Abstract AI provider."""

    name:  str = "base"
    model: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True if API key is configured."""
        ...

    @abstractmethod
    async def generate(
        self,
        prompt:     str,
        max_tokens: int   = 2000,
        temperature:float = 0.7,
    ) -> ProviderResult:
        """Generate text from prompt."""
        ...

    async def health_check(self) -> dict:
        """Quick test to verify provider works."""
        try:
            result = await self.generate("Réponds juste: OK", max_tokens=5)
            return {"status": "ok", "provider": self.name, "model": self.model}
        except Exception as e:
            return {"status": "error", "provider": self.name, "error": str(e)[:100]}
