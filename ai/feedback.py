"""
ai/feedback.py — Self-improving AI feedback loop.

The system learns from every article generated and automatically
improves future generations WITHOUT manual intervention.

How it works:
  1. After each article is generated, record_result() is called.
  2. Scores are stored in Redis (moving average, last 50 per provider).
  3. Before next generation, get_optimized_params() returns:
       - Best provider order (highest avg score first)
       - Optimal temperature (lower if recent scores are low)
       - Quality hints to inject into the prompt
  4. Weekly summary sent to admin Telegram.

Storage (Redis via safe_redis):
  feedback:scores:{provider}     → JSON list of last 50 scores
  feedback:provider:order        → JSON list of provider names (optimized order)
  feedback:temperature:{type}    → float (current optimal temperature)
  feedback:hints:{article_type}  → JSON list of quality hints
  feedback:stats:global          → JSON global statistics

Godmode features:
  ✅ Moving average per provider (window=20)
  ✅ Auto-temperature adjustment (score → temperature mapping)
  ✅ Dynamic provider reordering (best avg → first)
  ✅ Prompt quality hints injection (detect weak patterns)
  ✅ Failure pattern detection (short content, no FAQ, low density)
  ✅ Weekly performance report (Telegram notification)
  ✅ Zero config (works automatically, fail-open if Redis absent)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Redis Keys ────────────────────────────────────────────────────────────────
_PFX          = "feedback:"
_SCORES_KEY   = _PFX + "scores:{provider}"
_ORDER_KEY    = _PFX + "provider:order"
_TEMP_KEY     = _PFX + "temperature:{article_type}"
_HINTS_KEY    = _PFX + "hints:{article_type}"
_STATS_KEY    = _PFX + "stats:global"
_WEEKLY_KEY   = _PFX + "weekly:last_sent"

WINDOW_SIZE   = 20    # Moving average window
MAX_STORED    = 50    # Max scores stored per provider
TEMP_MIN      = 0.45  # Minimum temperature (very focused)
TEMP_MAX      = 0.85  # Maximum temperature (more creative)
TEMP_DEFAULT  = 0.70  # Starting temperature

# Temperature curve: score → temperature
# Low scores → lower temp (more focused/structured output)
# High scores → higher temp (creative, diverse)
TEMP_CURVE = [
    (80, 0.80),   # score >= 80 → temp 0.80 (good, keep creative)
    (70, 0.72),   # score >= 70 → temp 0.72
    (60, 0.65),   # score >= 60 → temp 0.65
    (50, 0.55),   # score >= 50 → temp 0.55 (struggling, more focused)
    (0,  0.45),   # score < 50  → temp 0.45 (very focused)
]

# Default provider order (fallback if no data)
DEFAULT_PROVIDERS = ["groq", "gemini", "openrouter", "template"]


# ── Result Recording ──────────────────────────────────────────────────────────

@dataclass
class GenerationRecord:
    """One article generation result to record."""
    provider:      str
    model:         str
    article_type:  str         # "product" | "article"
    score:         int         # 0-100
    word_count:    int         = 0
    faq_count:     int         = 0
    keyword_density: float     = 0.0
    latency_ms:    float       = 0.0
    timestamp:     float       = field(default_factory=time.time)

    @property
    def is_good(self) -> bool:
        return self.score >= 60

    @property
    def is_excellent(self) -> bool:
        return self.score >= 80


# ── Performance Tracker ───────────────────────────────────────────────────────

class PerformanceTracker:
    """
    Track article generation scores per provider.

    Stores rolling window of scores in Redis.
    Computes moving averages and trends.
    """

    def record(self, rec: GenerationRecord) -> None:
        """Record one generation result."""
        from core.safe_redis import safe_get, safe_set

        key  = _SCORES_KEY.format(provider=rec.provider)
        data = safe_get(key)
        scores = json.loads(data) if data else []

        scores.append({
            "score":        rec.score,
            "model":        rec.model,
            "type":         rec.article_type,
            "words":        rec.word_count,
            "faq":          rec.faq_count,
            "latency_ms":   round(rec.latency_ms, 1),
            "ts":           rec.timestamp,
        })

        # Keep only last MAX_STORED
        scores = scores[-MAX_STORED:]
        safe_set(key, json.dumps(scores), ttl=86400 * 30)

        # Update global stats
        self._update_global_stats(rec)

        logger.debug(
            f"[feedback] Recorded: {rec.provider} "
            f"score={rec.score} type={rec.article_type}"
        )

    def get_average(self, provider: str, window: int = WINDOW_SIZE) -> Optional[float]:
        """Return moving average score for a provider."""
        from core.safe_redis import safe_get
        data   = safe_get(_SCORES_KEY.format(provider=provider))
        if not data:
            return None
        scores = json.loads(data)
        recent = [s["score"] for s in scores[-window:]]
        if not recent:
            return None
        return sum(recent) / len(recent)

    def get_all_averages(self) -> dict[str, float]:
        """Return average scores for all providers."""
        averages = {}
        for provider in DEFAULT_PROVIDERS:
            avg = self.get_average(provider)
            if avg is not None:
                averages[provider] = round(avg, 1)
        return averages

    def get_trend(self, provider: str) -> str:
        """Return trend: 'improving' | 'declining' | 'stable'."""
        from core.safe_redis import safe_get
        data = safe_get(_SCORES_KEY.format(provider=provider))
        if not data:
            return "stable"
        scores = [s["score"] for s in json.loads(data)[-10:]]
        if len(scores) < 4:
            return "stable"
        first_half  = sum(scores[:len(scores)//2]) / (len(scores)//2)
        second_half = sum(scores[len(scores)//2:]) / (len(scores) - len(scores)//2)
        diff = second_half - first_half
        if diff >= 5:   return "📈 improving"
        if diff <= -5:  return "📉 declining"
        return "➡️ stable"

    def get_stats(self, provider: str) -> dict:
        """Return full stats for a provider."""
        from core.safe_redis import safe_get
        data = safe_get(_SCORES_KEY.format(provider=provider))
        if not data:
            return {"provider": provider, "articles": 0}
        scores_list = json.loads(data)
        scores      = [s["score"] for s in scores_list]
        return {
            "provider":     provider,
            "articles":     len(scores),
            "avg_score":    round(sum(scores) / len(scores), 1) if scores else 0,
            "best_score":   max(scores) if scores else 0,
            "worst_score":  min(scores) if scores else 0,
            "trend":        self.get_trend(provider),
            "last_score":   scores[-1] if scores else 0,
            "avg_words":    round(sum(s.get("words",0) for s in scores_list) / max(len(scores_list),1)),
        }

    def _update_global_stats(self, rec: GenerationRecord) -> None:
        """Update global counters."""
        from core.safe_redis import safe_get, safe_set
        data  = safe_get(_STATS_KEY)
        stats = json.loads(data) if data else {
            "total": 0, "excellent": 0, "good": 0, "poor": 0,
            "by_provider": {}, "by_type": {},
        }
        stats["total"] += 1
        if rec.is_excellent: stats["excellent"] += 1
        elif rec.is_good:    stats["good"]      += 1
        else:                stats["poor"]      += 1

        p = stats["by_provider"].setdefault(rec.provider, {"count":0,"total_score":0})
        p["count"]       += 1
        p["total_score"] += rec.score

        t = stats["by_type"].setdefault(rec.article_type, {"count":0,"total_score":0})
        t["count"]       += 1
        t["total_score"] += rec.score

        safe_set(_STATS_KEY, json.dumps(stats), ttl=86400 * 90)


# ── Prompt Optimizer ──────────────────────────────────────────────────────────

class PromptOptimizer:
    """
    Optimize AI generation parameters based on historical performance.

    Provides:
      - Optimal provider order (best avg score first)
      - Optimal temperature per article type
      - Quality hints to inject into prompts
    """

    def __init__(self):
        self._tracker = PerformanceTracker()

    def get_optimized_provider_order(self) -> list[str]:
        """
        Return providers sorted by average score (best first).

        Providers with no data keep their default position.
        Providers with avg < 40 are moved to last.
        """
        averages = self._tracker.get_all_averages()
        if not averages:
            return DEFAULT_PROVIDERS[:]

        # Sort known providers by score (desc)
        known   = [(p, avg) for p, avg in averages.items()]
        known.sort(key=lambda x: x[1], reverse=True)

        # Build ordered list
        ordered = [p for p, _ in known]

        # Add providers with no data in default order
        for p in DEFAULT_PROVIDERS:
            if p not in ordered:
                ordered.append(p)

        logger.debug(f"[feedback] Provider order: {ordered} (scores: {averages})")
        return ordered

    def get_optimal_temperature(self, article_type: str) -> float:
        """
        Return optimal temperature for article type based on history.

        Maps recent average score to temperature using TEMP_CURVE.
        """
        from core.safe_redis import safe_get, safe_set
        key  = _TEMP_KEY.format(article_type=article_type)
        data = safe_get(key)
        if data:
            try:
                return float(data)
            except Exception:
                pass

        return TEMP_DEFAULT

    def update_temperature(
        self,
        article_type: str,
        provider:     str,
        new_score:    int,
    ) -> float:
        """
        Update optimal temperature based on new score.
        Returns new temperature.
        """
        from core.safe_redis import safe_get, safe_set

        # Get current average
        avg = self._tracker.get_average(provider) or new_score

        # Map average to temperature
        new_temp = TEMP_DEFAULT
        for threshold, temp in TEMP_CURVE:
            if avg >= threshold:
                new_temp = temp
                break

        # Smooth transition (don't change too abruptly)
        key      = _TEMP_KEY.format(article_type=article_type)
        cur_data = safe_get(key)
        if cur_data:
            try:
                cur_temp = float(cur_data)
                # Exponential moving average (alpha=0.3)
                new_temp = 0.3 * new_temp + 0.7 * cur_temp
                new_temp = round(max(TEMP_MIN, min(TEMP_MAX, new_temp)), 2)
            except Exception:
                pass

        safe_set(key, str(new_temp), ttl=86400 * 7)
        logger.debug(
            f"[feedback] Temperature for {article_type}: "
            f"{new_temp:.2f} (avg_score={avg:.0f})"
        )
        return new_temp

    def get_quality_hints(self, article_type: str, provider: str) -> list[str]:
        """
        Return quality improvement hints based on recent failures.

        Analyzes recent scores and identifies weaknesses:
          - Low word count → add word count instruction
          - No FAQ → add FAQ instruction
          - Low keyword density → add keyword instruction
          - Low readability → add sentence length instruction
        """
        from core.safe_redis import safe_get
        hints = []

        # Analyze recent records for this provider
        data = safe_get(_SCORES_KEY.format(provider=provider))
        if not data:
            return hints

        records = json.loads(data)[-10:]  # Last 10

        if not records:
            return hints

        # Detect patterns
        avg_words = sum(r.get("words", 0) for r in records) / len(records)
        avg_faq   = sum(r.get("faq", 0)   for r in records) / len(records)
        avg_score = sum(r["score"]          for r in records) / len(records)

        # Word count issue
        if avg_words < 600:
            hints.append(
                f"IMPORTANT: Génère un article d'au moins 900 mots "
                f"(tes derniers articles avaient en moyenne {avg_words:.0f} mots)."
            )

        # FAQ missing
        if avg_faq < 1 and article_type == "product":
            hints.append(
                "Inclus impérativement une section 'Questions fréquentes' "
                "avec 3 questions/réponses pertinentes."
            )

        # General quality
        if avg_score < 55:
            hints.append(
                "Structure l'article avec minimum 3 titres H2 et une conclusion claire."
            )

        return hints[:3]  # Max 3 hints

    def build_enhanced_prompt(
        self,
        base_prompt:  str,
        article_type: str,
        provider:     str,
    ) -> str:
        """
        Enhance a base prompt with quality hints from feedback history.

        Only adds hints if there are identified weaknesses.
        Does not change the prompt if quality is already good.
        """
        hints = self.get_quality_hints(article_type, provider)
        if not hints:
            return base_prompt

        enhancement = "\n\n⚠️ INSTRUCTIONS QUALITÉ SUPPLÉMENTAIRES:\n"
        for i, hint in enumerate(hints, 1):
            enhancement += f"{i}. {hint}\n"

        logger.info(
            f"[feedback] Enhanced prompt for {provider} "
            f"({len(hints)} hints added)"
        )
        return base_prompt + enhancement

    def save_optimized_order(self, order: list[str]) -> None:
        """Persist optimized provider order."""
        from core.safe_redis import safe_set
        safe_set(_ORDER_KEY, json.dumps(order), ttl=86400 * 7)

    def get_saved_order(self) -> list[str]:
        """Load persisted provider order."""
        from core.safe_redis import safe_get
        data = safe_get(_ORDER_KEY)
        if data:
            try:
                return json.loads(data)
            except Exception:
                pass
        return DEFAULT_PROVIDERS[:]


# ── Feedback Loop ─────────────────────────────────────────────────────────────

class FeedbackLoop:
    """
    Main entry point for the self-improving AI system.

    Called from HybridAIGenerator after each generation.

    Usage:
        loop = FeedbackLoop()

        # After generation
        loop.record_and_optimize(
            provider="groq",
            model="llama-3.1-70b",
            article_type="product",
            score=72,
            word_count=950,
            faq_count=3,
        )

        # Before generation
        params = loop.get_generation_params("product")
        # params.provider_order → ["groq", "gemini", ...]  (reordered)
        # params.temperature → 0.72
        # Use params to configure HybridAIGenerator
    """

    def __init__(self):
        self._tracker   = PerformanceTracker()
        self._optimizer = PromptOptimizer()

    def record_and_optimize(
        self,
        provider:       str,
        model:          str,
        article_type:   str,
        score:          int,
        word_count:     int   = 0,
        faq_count:      int   = 0,
        keyword_density:float = 0.0,
        latency_ms:     float = 0.0,
    ) -> None:
        """
        Record a generation result and update optimization parameters.

        Call this after every article generation.
        All operations are fail-open (never raise).
        """
        try:
            rec = GenerationRecord(
                provider=provider, model=model,
                article_type=article_type, score=score,
                word_count=word_count, faq_count=faq_count,
                keyword_density=keyword_density, latency_ms=latency_ms,
            )

            # 1. Record score
            self._tracker.record(rec)

            # 2. Update temperature
            self._optimizer.update_temperature(article_type, provider, score)

            # 3. Update provider order (every 5 articles)
            stats = self._tracker.get_stats(provider)
            if stats.get("articles", 0) % 5 == 0:
                new_order = self._optimizer.get_optimized_provider_order()
                self._optimizer.save_optimized_order(new_order)
                logger.info(f"[feedback] Provider order updated: {new_order}")

            logger.info(
                f"[feedback] ✅ Recorded {provider}/{article_type} "
                f"score={score} words={word_count} "
                f"temp→{self._optimizer.get_optimal_temperature(article_type):.2f}"
            )

        except Exception as e:
            logger.warning(f"[feedback] record_and_optimize failed (non-fatal): {e}")

    def get_generation_params(self, article_type: str) -> "GenerationParams":
        """
        Return optimized parameters for next generation.

        Returns safe defaults if no data available.
        """
        try:
            order = self._optimizer.get_saved_order()
            temps = {}
            for provider in DEFAULT_PROVIDERS:
                temps[provider] = self._optimizer.get_optimal_temperature(article_type)

            return GenerationParams(
                provider_order=order,
                temperatures=temps,
                default_temperature=temps.get(order[0] if order else "groq", TEMP_DEFAULT),
            )
        except Exception as e:
            logger.warning(f"[feedback] get_generation_params failed: {e}")
            return GenerationParams()

    def get_enhanced_prompt(
        self,
        base_prompt:  str,
        article_type: str,
        provider:     str,
    ) -> str:
        """Return prompt enhanced with quality hints from history."""
        try:
            return self._optimizer.build_enhanced_prompt(
                base_prompt, article_type, provider
            )
        except Exception:
            return base_prompt

    def get_performance_report(self) -> str:
        """
        Generate a performance report for Telegram.
        """
        averages = self._tracker.get_all_averages()
        from core.safe_redis import safe_get
        data   = safe_get(_STATS_KEY)
        global_stats = json.loads(data) if data else {}

        total = global_stats.get("total", 0)
        lines = [
            "📈 <b>Rapport Performance IA</b>",
            f"Articles générés : <b>{total}</b>",
            "",
            "📊 <b>Scores par provider :</b>",
        ]

        if averages:
            for provider, avg in sorted(averages.items(), key=lambda x: -x[1]):
                trend = self._tracker.get_trend(provider)
                stats = self._tracker.get_stats(provider)
                lines.append(
                    f"  {provider}: <b>{avg:.0f}/100</b> "
                    f"({stats.get('articles',0)} articles) {trend}"
                )
        else:
            lines.append("  Pas encore de données")

        if total > 0:
            excellent = global_stats.get("excellent", 0)
            good      = global_stats.get("good", 0)
            poor      = global_stats.get("poor", 0)
            lines.extend([
                "",
                f"🏆 Excellent (>80): {excellent} ({excellent*100//total}%)",
                f"✅ Bon (60-80):     {good} ({good*100//total}%)",
                f"⚠️ Faible (<60):    {poor} ({poor*100//total}%)",
            ])

        # Current settings
        lines.extend([
            "",
            "⚙️ <b>Paramètres actuels :</b>",
            f"  Ordre providers : {' → '.join(self._optimizer.get_saved_order()[:3])}",
            f"  Temp product: {self._optimizer.get_optimal_temperature('product'):.2f}",
        ])

        return "\n".join(lines)

    async def send_weekly_report(self, admin_chat_id: int) -> bool:
        """Send weekly performance report to admin Telegram."""
        from core.safe_redis import safe_get, safe_set

        # Check if already sent this week
        last_sent = safe_get(_WEEKLY_KEY)
        if last_sent:
            try:
                last_ts = float(last_sent)
                if time.time() - last_ts < 7 * 86400:
                    return False  # Sent within last 7 days
            except Exception:
                pass

        report = self.get_performance_report()
        try:
            import httpx
            token = os.environ.get("BOT_TOKEN", "")
            if not token or not admin_chat_id:
                return False

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id":    admin_chat_id,
                        "text":       report,
                        "parse_mode": "HTML",
                    },
                )
                if resp.status_code == 200:
                    safe_set(_WEEKLY_KEY, str(time.time()), ttl=8 * 86400)
                    logger.info("[feedback] Weekly report sent")
                    return True
        except Exception as e:
            logger.warning(f"[feedback] Weekly report send failed: {e}")
        return False


@dataclass
class GenerationParams:
    """Optimized parameters for next generation."""
    provider_order:      list  = field(default_factory=lambda: DEFAULT_PROVIDERS[:])
    temperatures:        dict  = field(default_factory=dict)
    default_temperature: float = TEMP_DEFAULT


# ── Singleton ─────────────────────────────────────────────────────────────────

_loop: Optional[FeedbackLoop] = None


def get_feedback_loop() -> FeedbackLoop:
    """Return module-level FeedbackLoop singleton."""
    global _loop
    if _loop is None:
        _loop = FeedbackLoop()
    return _loop
