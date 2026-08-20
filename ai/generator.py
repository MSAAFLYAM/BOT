"""
ai/generator.py — Async AI article generator with quality scoring.

Architecture decisions:
  - Async OpenRouter API calls (httpx.AsyncClient, non-blocking).
  - Quality scoring after generation → auto-retry with better model if score < 60.
  - Model cascade: cheap → quality → premium (only if needed).
  - Max 2 generation attempts per article (cost control).
  - ArticleRequest is the single entry point — caller doesn't choose model.
    The generator selects the model based on article type and retry count.
  - Full pipeline: template → generate → score → optimize → return.
  - Generated article stored in DB (Article model — Phase 5 will publish).

Model selection strategy:
  Attempt 1: openai/gpt-4o-mini     (fast, cheap, ~95% quality)
  Attempt 2: anthropic/claude-haiku  (better, if score < 60)
  Configured via OPENROUTER_MODEL env var (override for specific deployments)

Cost control:
  - Max tokens: 2500 (enough for 1000-word article)
  - Temperature: 0.7 (creative but structured)
  - Max 2 attempts (never 3 — cost triples)

Error handling:
  - OpenRouter timeout (60s) → ArticleGenerationError
  - JSON parse error → ArticleGenerationError
  - Score < 40 after 2 attempts → ArticleGenerationError (don't publish garbage)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from scraping.amazon.parser import ProductData

from ai.scorer import ArticleScorer, ArticleScore
from ai.optimizer import SEOOptimizer, OptimizedArticle
from ai.templates import (
    ProductReviewTemplate,
    ComparisonTemplate,
    BuyingGuideTemplate,
)

logger = logging.getLogger(__name__)


# ── Custom exception ──────────────────────────────────────────────────────────

class ArticleGenerationError(Exception):
    def __init__(self, reason: str, score: Optional[int] = None):
        self.reason = reason
        self.score  = score
        super().__init__(reason)


# ── Article Request / Result ───────────────────────────────────────────────────

@dataclass
class ArticleRequest:
    """Input specification for article generation."""
    article_type:  str          # "product_review" | "comparison" | "buying_guide"
    title:         str
    keyword:       str          = ""
    language:      str          = "fr"
    affiliate_url: str          = ""
    affiliate_urls:list[str]    = field(default_factory=list)
    category:      str          = ""
    tags:          list[str]    = field(default_factory=list)

    # Type-specific data
    product_data:  Optional[dict] = None
    products:      list[dict]     = field(default_factory=list)  # for comparison


@dataclass
class ArticleResult:
    """Result of article generation."""
    request:       ArticleRequest
    article:       OptimizedArticle
    score:         ArticleScore
    model_used:    str              = ""
    attempts:      int              = 1
    generation_ms: float            = 0.0

    @property
    def success(self) -> bool:
        return self.score.should_publish

    def to_dict(self) -> dict:
        return {
            "title":        self.article.title,
            "html":         self.article.html,
            "meta":         self.article.meta_description,
            "slug":         self.article.slug,
            "word_count":   self.article.word_count,
            "read_time":    self.article.reading_time_min,
            "score":        self.score.to_dict(),
            "model":        self.model_used,
            "attempts":     self.attempts,
            "gen_ms":       round(self.generation_ms, 1),
            "affiliate_url":self.article.affiliate_url,
            "tags":         self.article.tags,
            "category":     self.article.category,
        }


# ── AI Generator ─────────────────────────────────────────────────────────────

class AIGenerator:
    """
    Async AI article generator with quality scoring.

        Usage:
            gen = AIGenerator()

            # Product review
            result = await gen.generate_product_review(product_data, affiliate_url="...")
            if result.success:
                publish(result.article.html)

            # Product review
            result = await gen.generate_product_review(product_data)
    """

    # Model cascade: paid first, then free fallbacks
    # Free models (marked :free) have no cost on OpenRouter
    MODELS = [
        "openai/gpt-4o-mini",              # Attempt 1: fast + cheap
        "anthropic/claude-haiku-4-5",      # Attempt 2: better quality
    ]
    # Free tier models — tried in order until one works
    # Updated list of verified OpenRouter free models
    FREE_MODELS = [
        "meta-llama/llama-3.1-8b-instruct:free",
        "meta-llama/llama-3.2-3b-instruct:free",
        "mistralai/mistral-7b-instruct:free",
        "google/gemma-2-9b-it:free",
        "qwen/qwen-2-7b-instruct:free",
        "microsoft/phi-3-mini-128k-instruct:free",
    ]

    def __init__(self):
        self._scorer   = ArticleScorer()
        self._optimizer= SEOOptimizer()

    # ── Public API ────────────────────────────────────────────────────────────

    async def generate_product_review(
        self,
        product:       "ProductData",
        affiliate_url: str   = "",
        keyword:       str   = "",
        language:      str   = "fr",
    ) -> ArticleResult:
        """Generate an Amazon product review article."""
        kw = keyword or f"avis {product.title}"

        request = ArticleRequest(
            article_type="product_review",
            title=product.title,
            keyword=kw,
            language=language,
            affiliate_url=affiliate_url or product.affiliate_link,
            category=product.category or "Produits",
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
            marketplace=product.marketplace,
        )

        return await self._generate_with_retry(request, template.build())

    async def generate_comparison(
        self,
        products:      list[dict],
        category:      str,
        keyword:       str   = "",
        language:      str   = "fr",
        affiliate_urls:list[str] = None,
    ) -> ArticleResult:
        """Generate a product comparison article."""
        kw = keyword or f"meilleur {category}"

        request = ArticleRequest(
            article_type="comparison",
            title=f"Comparatif : {category}",
            keyword=kw,
            language=language,
            affiliate_urls=affiliate_urls or [],
            category=category,
            products=products,
        )

        template = ComparisonTemplate(
            products=products,
            category=category,
            keywords=[kw],
            language=language,
        )

        return await self._generate_with_retry(request, template.build())

    async def generate_buying_guide(
        self,
        category:      str,
        products:      list[dict] = None,
        keyword:       str        = "",
        criteria:      list[str]  = None,
        target_buyer:  str        = "grand public",
        language:      str        = "fr",
        affiliate_urls:list[str]  = None,
    ) -> ArticleResult:
        """Generate a buying guide article."""
        kw = keyword or f"guide achat {category}"

        request = ArticleRequest(
            article_type="buying_guide",
            title=f"Guide d'achat : {category}",
            keyword=kw,
            language=language,
            affiliate_urls=affiliate_urls or [],
            category=category,
            products=products or [],
        )

        template = BuyingGuideTemplate(
            category=category,
            products=products or [],
            criteria=criteria or [],
            keywords=[kw],
            target_buyer=target_buyer,
            language=language,
        )

        return await self._generate_with_retry(request, template.build())

    # ── Core generation pipeline ──────────────────────────────────────────────

    async def _generate_with_retry(
        self,
        request:  ArticleRequest,
        prompt:   str,
        max_attempts: int = 2,
    ) -> ArticleResult:
        """
        Generate with automatic quality-based retry.

        Attempt 1: fast cheap model
        Attempt 2 (if score < 60): better quality model

        Raises ArticleGenerationError if quality still insufficient after retries.
        """
        start = time.monotonic()
        last_score:   Optional[ArticleScore]   = None
        last_article: Optional[OptimizedArticle] = None

        for attempt in range(1, max_attempts + 1):
            model = self._select_model(attempt)
            logger.info(
                f"[ai] Generating {request.article_type} | "
                f"attempt={attempt} | model={model}"
            )

            try:
                raw_html = await self._call_openrouter(prompt, model)
            except ArticleGenerationError as e:
                if "CREDITS_EXHAUSTED" in str(e):
                    # Try each free model in sequence until one works
                    logger.warning(f"[ai] Credits exhausted on {model} — trying free models")
                    raw_html = await self._try_free_models(prompt)
                    if raw_html:
                        model = "free-model"
                    else:
                        if attempt == max_attempts:
                            raise ArticleGenerationError(
                                "Tous les modèles ont échoué. "
                                "Rechargez vos crédits OpenRouter ou définissez "
                                "OPENROUTER_FREE_MODEL environment variable."
                            )
                        await asyncio.sleep(2)
                        continue
                else:
                    logger.warning(f"[ai] OpenRouter error (attempt {attempt}): {e}")
                    if attempt == max_attempts:
                        raise ArticleGenerationError(f"OpenRouter failed: {e}")
                    await asyncio.sleep(2)
                    continue
            except Exception as e:
                logger.warning(f"[ai] OpenRouter error (attempt {attempt}): {e}")
                if attempt == max_attempts:
                    raise ArticleGenerationError(f"OpenRouter failed: {e}")
                await asyncio.sleep(2)
                continue

            # Optimize (clean, inject links, meta, slug)
            article = self._optimizer.optimize(
                html=raw_html,
                title=request.title,
                keyword=request.keyword,
                affiliate_url=request.affiliate_url,
                affiliate_urls=request.affiliate_urls,
                category=request.category,
                tags=request.tags,
            )

            # Score
            score = self._scorer.score(article.html, request.keyword)
            last_score   = score
            last_article = article

            logger.info(
                f"[ai] Score: {score.total}/100 (grade={score.grade}) | "
                f"words={score.word_count} | "
                f"model={model} | attempt={attempt}"
            )

            if score.should_publish:
                elapsed = (time.monotonic() - start) * 1000
                return ArticleResult(
                    request=request,
                    article=article,
                    score=score,
                    model_used=model,
                    attempts=attempt,
                    generation_ms=elapsed,
                )

            if score.should_regenerate and attempt < max_attempts:
                logger.warning(
                    f"[ai] Score too low ({score.total}), retrying with better model..."
                )
                # Add quality improvement instructions to prompt
                prompt = self._add_quality_instructions(prompt, score)

        # All attempts exhausted
        elapsed = (time.monotonic() - start) * 1000
        if last_score and last_article and last_score.total >= 40:
            # Return best result even if not perfect (score 40-59 = "fair")
            logger.warning(
                f"[ai] Publishing with score {last_score.total} (below 60 threshold)"
            )
            return ArticleResult(
                request=request,
                article=last_article,
                score=last_score,
                model_used=self._select_model(max_attempts),
                attempts=max_attempts,
                generation_ms=elapsed,
            )

        raise ArticleGenerationError(
            reason=f"Article quality insufficient after {max_attempts} attempts",
            score=last_score.total if last_score else 0,
        )

    def _add_quality_instructions(self, prompt: str, score: ArticleScore) -> str:
        """Add quality improvement instructions based on score issues."""
        additions = "\n\nAMÉLIORATIONS REQUISES POUR CETTE VERSION :\n"
        if score.word_count < 800:
            additions += f"- IMPORTANT: Écrire au minimum 900 mots (version précédente: {score.word_count} mots)\n"
        if score.h2_count < 3:
            additions += "- Ajouter plus de titres <h2> (minimum 3 sections)\n"
        if not score.has_conclusion:
            additions += "- Ajouter une section Conclusion ou Notre Verdict\n"
        if score.keyword_density < 1.0:
            additions += "- Utiliser le mot-clé plus souvent dans le texte\n"
        if score.avg_sentence_len > 30:
            additions += "- Raccourcir les phrases (maximum 25 mots par phrase)\n"
        return prompt + additions

    # ── Model Selection ───────────────────────────────────────────────────────

    def _select_model(self, attempt: int) -> str:
        """Select model based on attempt number."""
        try:
            import os
            configured = os.environ.get("OPENROUTER_MODEL", "")
            if configured and attempt == 1:
                return configured
        except Exception:
            pass

        idx = min(attempt - 1, len(self.MODELS) - 1)
        return self.MODELS[idx]

    async def _try_free_models(self, prompt: str) -> str:
        """
        Try each free model in sequence until one succeeds.
        Skips 404 (not found) and moves to the next.
        Returns generated text or empty string if all fail.
        """
        import os
        # Check if user set a specific free model preference
        free_pref = os.environ.get("OPENROUTER_FREE_MODEL", "")
        models_to_try = ([free_pref] if free_pref else []) + self.FREE_MODELS

        for free_model in models_to_try:
            try:
                logger.info(f"[ai] Trying free model: {free_model}")
                result = await self._call_openrouter(
                    prompt, free_model, max_tokens=2000, timeout=60
                )
                if result:
                    logger.info(f"[ai] ✅ Free model succeeded: {free_model}")
                    return result
            except ArticleGenerationError as e:
                err = str(e)
                if "404" in err or "No endpoints" in err:
                    logger.warning(f"[ai] {free_model} not found, trying next...")
                    continue
                elif "402" in err or "CREDITS" in err:
                    logger.warning(f"[ai] {free_model} needs credits too, trying next...")
                    continue
                else:
                    logger.warning(f"[ai] {free_model} error: {err[:80]}")
                    continue
            except Exception as e:
                logger.warning(f"[ai] {free_model} failed: {str(e)[:80]}")
                continue

        return ""  # All free models failed

    # ── OpenRouter API Call ───────────────────────────────────────────────────

    async def _call_openrouter(
        self,
        prompt:     str,
        model:      str,
        max_tokens: int = 2500,
        temperature:float = 0.7,
        timeout:    int = 90,
    ) -> str:
        """
        Async call to OpenRouter API.

        Returns the generated text content.
        Raises on HTTP error or timeout.
        """
        import httpx
        import os
        api_key = os.environ.get("OPENROUTER_API_KEY", "")

        if not api_key:
            raise ArticleGenerationError("OPENROUTER_API_KEY not configured")

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization":  f"Bearer {api_key}",
                    "Content-Type":   "application/json",
                    "HTTP-Referer":   "https://amazon-bot-pin.example.com",
                    "X-Title":        "Amazon Affiliate Bot",
                },
                json={
                    "model":       model,
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  max_tokens,
                    "temperature": temperature,
                },
            )

            if response.status_code == 402:
                # Insufficient credits → try free model automatically
                raise ArticleGenerationError(
                    f"CREDITS_EXHAUSTED:{model}",
                )
            if response.status_code != 200:
                raise ArticleGenerationError(
                    f"OpenRouter HTTP {response.status_code}: {response.text[:200]}"
                )

            data    = response.json()
            content = data["choices"][0]["message"]["content"]

            if not content or len(content) < 100:
                raise ArticleGenerationError(
                    f"OpenRouter returned empty response (model={model})"
                )

            return content


# ── Module-level singleton ────────────────────────────────────────────────────

_generator: Optional[AIGenerator] = None


def get_generator() -> AIGenerator:
    """Return module-level AIGenerator singleton."""
    global _generator
    if _generator is None:
        _generator = AIGenerator()
    return _generator
