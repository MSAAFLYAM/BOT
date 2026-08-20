"""
ai/hybrid.py — HybridAIGenerator: multi-provider orchestrator.

Provider cascade (best → fallback):
  1. Groq           (FREE, ultra-fast: Llama 3.1 70B)
  2. Google Gemini  (FREE, high quality: Gemini 1.5 Flash)
  3. OpenRouter     (paid + free cascade)
  4. Python Template(ALWAYS works, no API)

Each provider is tried until one succeeds.
Providers with no API key are skipped automatically.

Usage:
    gen = HybridAIGenerator()
    result = await gen.generate_product_review(product_data)

Configuration:
    GROQ_API_KEY    = gsk_...        ← Free at console.groq.com
    GEMINI_API_KEY  = AIzaSy...      ← Free at aistudio.google.com
    OPENROUTER_API_KEY = sk-or-...   ← OpenRouter (optional)

Priority override:
    AI_PROVIDER_ORDER = groq,gemini,openrouter,template
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from scraping.amazon.parser import ProductData

from ai.scorer import ArticleScorer
from ai.optimizer import SEOOptimizer
from ai.templates import (
    ProductReviewTemplate,
    ComparisonTemplate, BuyingGuideTemplate,
)
from ai.providers.base import BaseProvider, ProviderResult
from ai.providers.circuit_breaker import get_breaker, get_all_statuses
from ai.feedback import get_feedback_loop, GenerationParams
from ai.generator import ArticleGenerationError, ArticleResult, ArticleRequest

logger = logging.getLogger(__name__)


def _build_providers() -> list[BaseProvider]:
    """
    Build ordered list of providers based on available API keys.

    Cascade (meilleur → fallback):
      1. Groq          — gratuit, ultra-rapide
      2. Gemini         — gratuit, 1M tokens/jour
      3. LiteLLM        — unifié (Groq+Gemini+CF+OpenRouter en 1)
      4. OpenRouter     — gratuit + payant
      5. Cloudflare AI  — gratuit 10K req/jour
      6. Template       — toujours disponible (fallback ultime)
    """
    from ai.providers.groq       import GroqProvider
    from ai.providers.gemini     import GeminiProvider
    from ai.providers.openrouter import OpenRouterProvider
    from ai.providers.template   import TemplateProvider

    # LiteLLM (optionnel — graceful fallback si non installé)
    LiteLLMProvider = None
    try:
        from ai.providers.litellm_provider import LiteLLMProvider
    except ImportError:
        pass

    # Cloudflare AI (optionnel)
    CloudflareAIProvider = None
    try:
        from ai.providers.cloudflare_ai import CloudflareAIProvider
    except ImportError:
        pass

    # Default order — LiteLLM après Groq/Gemini
    default_order = ["groq", "gemini", "litellm", "openrouter", "cloudflare", "template"]

    # Override via env var: AI_PROVIDER_ORDER=groq,gemini,template
    order_env = os.environ.get("AI_PROVIDER_ORDER", "")
    if order_env:
        default_order = [p.strip() for p in order_env.split(",")]

    all_providers: dict = {
        "groq":       GroqProvider(),
        "gemini":     GeminiProvider(),
        "openrouter": OpenRouterProvider(),
        "template":   TemplateProvider(),
    }

    # Ajouter LiteLLM si disponible
    if LiteLLMProvider:
        all_providers["litellm"] = LiteLLMProvider()

    # Ajouter Cloudflare si disponible
    if CloudflareAIProvider:
        cf = CloudflareAIProvider()
        if cf.is_configured():
            all_providers["cloudflare"] = cf

    providers = []
    for name in default_order:
        p = all_providers.get(name)
        if p:
            if p.is_available():
                providers.append(p)
                logger.debug(f"[hybrid] Provider ready: {name}")
            else:
                logger.debug(f"[hybrid] Provider skipped (no API key): {name}")

    # Template est toujours en dernier (fallback ultime)
    template = all_providers["template"]
    if not any(p.name == "template" for p in providers):
        providers.append(template)

    if providers:
        logger.info(f"[hybrid] Cascade: {[p.name for p in providers]}")
    return providers


class HybridAIGenerator:
    """
    Multi-provider AI article generator.

    Tries providers in order until one produces a quality article.
    Falls back to Python template if all AI providers fail.

    Always succeeds (template provider is always available).
    """

    def __init__(self):
        self._providers = _build_providers()
        self._scorer    = ArticleScorer()
        self._optimizer = SEOOptimizer()

    def get_active_providers(self) -> list[str]:
        return [p.name for p in self._providers]

    def get_circuit_status(self) -> list[dict]:
        return get_all_statuses()

    def reset_circuit(self, provider_name: str) -> None:
        get_breaker(provider_name).reset()

    async def generate_product_review(
        self,
        product:       "ProductData",
        affiliate_url: str   = "",
        keyword:       str   = "",
        language:      str   = "fr",
    ) -> ArticleResult:
        """Generate product review using best available provider."""
        kw = keyword or f"avis {product.title}"

        request = ArticleRequest(
            article_type="product_review",
            title=product.title,
            keyword=kw,
            language=language,
            affiliate_url=affiliate_url or product.affiliate_link,
        )

        template = ProductReviewTemplate(
            title=product.title,
            asin=product.asin,
            price=str(product.price) if product.price else None,
            rating=product.rating,
            reviews_count=product.reviews_count,
            brand=product.brand,
            category=product.category,
            description=product.short_description,
            keywords=[kw],
            language=language,
        )

        return await self._generate(request, template.build())

    async def _generate(
        self,
        request: ArticleRequest,
        prompt:  str,
    ) -> ArticleResult:
        """
        Try each provider until one produces a publishable article.

        Minimum quality score: 40/100 (template typically scores ~55).
        """
        start        = time.monotonic()
        best_result  = None
        best_score   = 0

        # Use feedback-optimized provider order
        feedback     = get_feedback_loop()
        fb_params    = feedback.get_generation_params(request.article_type)
        ordered_providers = sorted(
            self._providers,
            key=lambda p: (
                fb_params.provider_order.index(p.name)
                if p.name in fb_params.provider_order
                else 99
            ),
        )
        for provider in ordered_providers:
            provider_name = provider.name
            try:
                logger.info(f"[hybrid] Trying provider: {provider_name}")
                breaker = get_breaker(provider_name)
                if not breaker.can_attempt():
                    st = breaker.get_status()
                    logger.warning(f"[hybrid] {provider_name} OPEN — skip (reopen in {st.get('reopen_in_s','?')}s)")
                    continue
                opt_temp    = fb_params.temperatures.get(
                    provider.name, fb_params.default_temperature
                )
                # Enhance prompt with quality hints
                enhanced = feedback.get_enhanced_prompt(
                    prompt, request.article_type, provider.name
                )
                prov_result = await provider.generate(
                    enhanced, max_tokens=2500, temperature=opt_temp
                )

                if not prov_result.success or not prov_result.text:
                    logger.warning(
                        f"[hybrid] {provider_name} failed: {prov_result.error}"
                    )
                    breaker.record_failure()
                    continue
                breaker.record_success()

                # Optimize article
                article = self._optimizer.optimize(
                    html=prov_result.text,
                    title=request.title,
                    keyword=request.keyword,
                    affiliate_url=request.affiliate_url,
                    category=request.category,
                    tags=request.tags,
                )

                # Score it
                score = self._scorer.score(article.html, request.keyword)

                logger.info(
                    f"[hybrid] {provider_name}/{prov_result.model} "
                    f"→ score={score.total}/100, words={score.word_count}"
                )

                # Update feedback with real score — wrapped to never block generation
                try:
                    feedback.record_and_optimize(
                        provider=provider_name,
                        model=prov_result.model,
                        article_type=request.article_type,
                        score=score.total,
                        word_count=getattr(score, 'word_count', 0),
                        faq_count=getattr(score, 'faq_count', 0),
                        keyword_density=getattr(score, 'keyword_density', 0.0),
                        latency_ms=prov_result.latency_ms,
                    )
                except Exception as _fb_err:
                    logger.debug(f"[hybrid] feedback error (non-blocking): {_fb_err}")

                # Keep best result
                if score.total > best_score:
                    best_score  = score.total
                    elapsed     = (time.monotonic() - start) * 1000
                    best_result = ArticleResult(
                        request=request,
                        article=article,
                        score=score,
                        model_used=f"{provider_name}/{prov_result.model}",
                        attempts=self._providers.index(provider) + 1,
                        generation_ms=elapsed,
                    )

                # Good enough? Stop here
                if score.total >= 60:
                    return best_result

                # Template score (40-60) is acceptable as last resort
                if provider_name == "template" and score.total >= 35:
                    return best_result

            except Exception as e:
                logger.warning(f"[hybrid] {provider_name} exception: {e}")
                get_breaker(provider_name).record_failure()
                continue

        if best_result:
            logger.info(
                f"[hybrid] Best result: {best_result.model_used} "
                f"score={best_result.score.total}"
            )
            return best_result

        # Should never reach here (template always works)
        raise ArticleGenerationError("Tous les providers ont échoué", score=0)


# ── Singleton ─────────────────────────────────────────────────────────────────

_hybrid: Optional[HybridAIGenerator] = None


def get_hybrid_generator() -> HybridAIGenerator:
    """Return module-level HybridAIGenerator singleton."""
    global _hybrid
    if _hybrid is None:
        _hybrid = HybridAIGenerator()
    return _hybrid
