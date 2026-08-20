"""
ai/dedup.py — Semantic deduplication engine.

Prevents publishing near-duplicate articles by comparing
semantic embeddings using cosine similarity.

Architecture:
  - Model: all-MiniLM-L6-v2 (80MB, 384-dim embeddings)
    Best balance: speed, quality, RAM usage.
  - Storage: Redis (JSON arrays, TTL 90 days)
    Chosen over FAISS (too heavy) and ChromaDB (persistent disk needed).
  - Two-stage check:
    Stage 1 — Title similarity (fast, ~5ms)
    Stage 2 — Content similarity (thorough, ~50ms)
  - Lazy model loading: model loaded only on first use,
    not at import time (preserves startup speed).

Thresholds:
  > 0.95 = near-identical (same article, different words)
  > 0.85 = very similar (duplicate topic + structure)
  > 0.70 = related (same topic, different angle — OK to publish)
  < 0.70 = original content — publish freely

Usage:
    dedup = SemanticDeduplicator()

    # Check before generating
    result = await dedup.check(title="Recette Tarte aux Pommes",
                               content_preview="Découvrez notre recette...")
    if result.is_duplicate:
        logger.info(f"Skipping: {result.similar_to} (sim={result.similarity:.2f})")
        continue

    # After publishing, register the article
    await dedup.register(article_id="uuid-123",
                         title="Recette Tarte aux Pommes",
                         content=article_html)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL_NAME      = "all-MiniLM-L6-v2"
THRESHOLD_HIGH  = 0.95   # Near-identical → definitely duplicate
THRESHOLD_MED   = 0.85   # Very similar   → duplicate
THRESHOLD_LOW   = 0.70   # Related        → OK to publish

REDIS_PREFIX    = "dedup:embedding:"
REDIS_INDEX_KEY = "dedup:index"        # Set of all stored article IDs
REDIS_TTL       = 90 * 86400           # 90 days


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class DedupResult:
    """Result of a duplicate check."""
    is_duplicate:   bool
    similarity:     float          = 0.0
    similar_to:     Optional[str]  = None   # article_id of most similar
    similar_title:  Optional[str]  = None
    threshold_used: float          = THRESHOLD_MED
    check_ms:       float          = 0.0

    @property
    def verdict(self) -> str:
        if not self.is_duplicate:
            return "✅ Original"
        if self.similarity >= THRESHOLD_HIGH:
            return "🔴 Quasi-identique"
        return "🟡 Très similaire"

    def to_dict(self) -> dict:
        return {
            "is_duplicate":  self.is_duplicate,
            "similarity":    round(self.similarity, 3),
            "similar_to":    self.similar_to,
            "similar_title": self.similar_title,
            "verdict":       self.verdict,
            "check_ms":      round(self.check_ms, 1),
        }


# ── Semantic Deduplicator ─────────────────────────────────────────────────────

class SemanticDeduplicator:
    """
    Semantic duplicate detection using sentence embeddings.

    Lazy-loads the ML model on first use.
    All state stored in Redis (no local disk needed).
    """

    def __init__(
        self,
        threshold:      float = THRESHOLD_MED,
        model_name:     str   = MODEL_NAME,
        enabled:        bool  = True,
    ):
        self._threshold  = threshold
        self._model_name = model_name
        self._enabled    = enabled
        self._model      = None   # Lazy loaded

    # ── Public API ────────────────────────────────────────────────────────────

    async def check(
        self,
        title:           str,
        content_preview: str   = "",
        threshold:       Optional[float] = None,
    ) -> DedupResult:
        """
        Check if an article is a duplicate of something already stored.

        Args:
            title:           Article title (used for quick check)
            content_preview: First 500 chars of content (used for thorough check)
            threshold:       Override default similarity threshold

        Returns:
            DedupResult with is_duplicate=True if similar content found.
        """
        if not self._enabled:
            return DedupResult(False, check_ms=0.0)

        start     = time.monotonic()
        threshold = threshold or self._threshold

        try:
            # Stage 1: Check title hash (exact duplicate, instant)
            title_hash = self._hash(title)
            if await self._hash_exists(title_hash):
                elapsed = (time.monotonic() - start) * 1000
                logger.info(f"[dedup] Exact title match: {title[:50]}")
                return DedupResult(
                    is_duplicate=True,
                    similarity=1.0,
                    threshold_used=threshold,
                    check_ms=elapsed,
                )

            # Stage 2: Semantic similarity
            text_to_check = f"{title}. {content_preview}"[:600]
            query_emb     = await self._encode(text_to_check)

            if query_emb is None:
                # Model not available → fail open (don't block)
                elapsed = (time.monotonic() - start) * 1000
                return DedupResult(False, check_ms=elapsed)

            # Compare against all stored embeddings
            best_sim    = 0.0
            best_id     = None
            best_title  = None

            stored_ids = await self._get_all_ids()
            for article_id in stored_ids[:500]:  # Cap at 500 comparisons
                stored = await self._get_embedding(article_id)
                if stored is None:
                    continue
                sim = self._cosine_similarity(query_emb, stored["embedding"])
                if sim > best_sim:
                    best_sim   = sim
                    best_id    = article_id
                    best_title = stored.get("title", "")

            elapsed = (time.monotonic() - start) * 1000
            is_dup  = best_sim >= threshold

            if is_dup:
                logger.warning(
                    f"[dedup] 🔴 Duplicate detected: {title[:50]!r} "
                    f"≈ {best_title[:50]!r} (similarity={best_sim:.3f})"
                )
            else:
                logger.debug(
                    f"[dedup] ✅ Original: {title[:50]!r} "
                    f"(best_sim={best_sim:.3f} < {threshold})"
                )

            return DedupResult(
                is_duplicate=is_dup,
                similarity=best_sim,
                similar_to=best_id,
                similar_title=best_title,
                threshold_used=threshold,
                check_ms=elapsed,
            )

        except Exception as e:
            logger.warning(f"[dedup] Check failed (fail open): {e}")
            elapsed = (time.monotonic() - start) * 1000
            return DedupResult(False, check_ms=elapsed)  # Fail open

    async def register(
        self,
        article_id: str,
        title:      str,
        content:    str,
        metadata:   Optional[dict] = None,
    ) -> bool:
        """
        Register a published article in the dedup store.

        Call this AFTER successfully publishing an article.
        The embedding is computed and stored in Redis.

        Args:
            article_id: Unique ID (UUID or slug)
            title:      Article title
            content:    Full HTML or text content
            metadata:   Optional extra info (url, platform, etc.)

        Returns:
            True if registered successfully.
        """
        try:
            # Store title hash for exact matching
            await self._store_hash(self._hash(title))

            # Compute and store embedding
            text = f"{title}. {self._extract_text(content)[:500]}"
            emb  = await self._encode(text)

            if emb is None:
                logger.warning(f"[dedup] Model unavailable, skipping registration")
                return False

            record = {
                "title":      title,
                "embedding":  emb,
                "registered": time.time(),
                "metadata":   metadata or {},
            }
            await self._store_embedding(article_id, record)
            await self._add_to_index(article_id)

            logger.info(f"[dedup] Registered: {article_id} ({title[:50]!r})")
            return True

        except Exception as e:
            logger.warning(f"[dedup] Registration failed: {e}")
            return False

    async def get_stats(self) -> dict:
        """Return deduplication statistics."""
        try:
            ids   = await self._get_all_ids()
            count = len(ids)
            return {
                "model":          self._model_name,
                "threshold":      self._threshold,
                "articles_indexed": count,
                "enabled":        self._enabled,
            }
        except Exception:
            return {"enabled": self._enabled, "articles_indexed": 0}

    # ── ML Model ──────────────────────────────────────────────────────────────

    async def _encode(self, text: str) -> Optional[list]:
        """
        Encode text to embedding vector.
        Lazy-loads model on first call.
        Returns list of floats or None if model unavailable.
        """
        import asyncio
        try:
            model = await self._get_model()
            if model is None:
                return None

            def _sync_encode():
                embeddings = model.encode([text], normalize_embeddings=True)
                return embeddings[0].tolist()

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _sync_encode)

        except Exception as e:
            logger.warning(f"[dedup] Encode failed: {e}")
            return None

    async def _get_model(self):
        """Lazy-load sentence-transformers model."""
        if self._model is not None:
            return self._model

        import asyncio

        def _load():
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(f"[dedup] Loading model: {self._model_name}...")
                model = SentenceTransformer(self._model_name)
                logger.info(f"[dedup] Model loaded ✅")
                return model
            except ImportError:
                logger.warning(
                    "[dedup] sentence-transformers not installed. "
                    "Run: pip install sentence-transformers"
                )
                return None
            except Exception as e:
                logger.warning(f"[dedup] Model load failed: {e}")
                return None

        loop        = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(None, _load)
        return self._model

    # ── Math ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _cosine_similarity(a: list, b: list) -> float:
        """Cosine similarity between two normalized vectors."""
        try:
            if not a or not b or len(a) != len(b):
                return 0.0
            dot = sum(x * y for x, y in zip(a, b))
            # Vectors are pre-normalized → dot product = cosine similarity
            return min(1.0, max(-1.0, dot))
        except Exception:
            return 0.0

    # ── Redis helpers ─────────────────────────────────────────────────────────

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis as _redis
                url         = os.environ.get("REDIS_URL","redis://localhost:6379")
                self._redis = _redis.from_url(url, decode_responses=True)
            except Exception as e:
                logger.warning(f"[dedup] Redis unavailable: {e}")
        return self._redis

    async def _get_all_ids(self) -> list:
        from core.safe_redis import safe_smembers
        return list(safe_smembers(REDIS_INDEX_KEY))

    async def _add_to_index(self, article_id: str) -> None:
        from core.safe_redis import safe_sadd
        safe_sadd(REDIS_INDEX_KEY, article_id, ttl=REDIS_TTL)

    async def _store_embedding(self, article_id: str, record: dict) -> None:
        from core.safe_redis import safe_set
        key = f"{REDIS_PREFIX}{article_id}"
        safe_set(key, json.dumps(record, default=str), ttl=REDIS_TTL)

    async def _get_embedding(self, article_id: str) -> Optional[dict]:
        from core.safe_redis import safe_get
        data = safe_get(f"{REDIS_PREFIX}{article_id}")
        return json.loads(data) if data else None

    async def _hash_exists(self, hash_str: str) -> bool:
        from core.safe_redis import safe_sismember
        return safe_sismember(f"{REDIS_INDEX_KEY}:hashes", hash_str)

    async def _store_hash(self, hash_str: str) -> None:
        from core.safe_redis import safe_sadd
        safe_sadd(f"{REDIS_INDEX_KEY}:hashes", hash_str, ttl=REDIS_TTL)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.lower().strip().encode()).hexdigest()[:32]

    @staticmethod
    def _extract_text(html: str) -> str:
        """Strip HTML tags for text comparison."""
        import re
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


# ── Singleton ─────────────────────────────────────────────────────────────────

_deduplicator: Optional[SemanticDeduplicator] = None


def get_deduplicator() -> SemanticDeduplicator:
    """Return module-level deduplicator singleton."""
    global _deduplicator
    if _deduplicator is None:
        _deduplicator = SemanticDeduplicator()
    return _deduplicator
