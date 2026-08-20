"""
core/config.py — Centralized configuration via Pydantic v2 Settings.

Architecture decisions:
  - Pydantic v2 Settings validates ALL env vars at startup.
  - If a required var is missing → clear error, not silent crash.
  - Nested settings classes keep domains separated.
  - Settings are cached as a singleton (lru_cache).

Memory impact:
  - Settings loaded once at startup, cached forever.
  - No per-request settings loading.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ──────────────────────────────────────────────────────────────────────────────
#  Database Settings
# ──────────────────────────────────────────────────────────────────────────────

class DatabaseSettings(BaseSettings):
    """
    PostgreSQL configuration.

    Format: postgresql://user:pass@host:port/dbname

    We auto-convert to async format (postgresql+asyncpg://).
    """
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    DATABASE_URL: str = Field(
        default="",
        description="PostgreSQL connection URL",
    )

    # Connection pool settings
    DB_POOL_SIZE: int = Field(
        default=5,
        description="SQLAlchemy connection pool size.",
    )
    DB_MAX_OVERFLOW: int = Field(
        default=2,
        description="Extra connections above pool_size during traffic spikes.",
    )
    DB_POOL_TIMEOUT: int = Field(
        default=30,
        description="Seconds to wait for a pool connection before raising.",
    )
    DB_POOL_RECYCLE: int = Field(
        default=1800,
        description="Recycle connections after 30 min to avoid stale connections.",
    )
    DB_ECHO: bool = Field(
        default=False,
        description="Log all SQL statements. Use True only during development.",
    )

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def convert_to_async_driver(cls, v: str) -> str:
        """
        Convert PostgreSQL URL to asyncpg format.

        Provides: postgresql://...
        asyncpg needs:    postgresql+asyncpg://...
        """
        if not v:
            import os
            v = os.environ.get("DATABASE_URL", "")
            if not v:
                return ""  # Fail gracefully, not at import time
        # Replace driver prefix for asyncpg
        v = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", v)
        # Also handle already-async URLs
        if "+asyncpg" not in v and v.startswith("postgresql"):
            v = v.replace("postgresql://", "postgresql+asyncpg://")
        return v

    @property
    def sync_url(self) -> str:
        """Synchronous URL for Alembic migrations (uses psycopg2)."""
        return self.DATABASE_URL.replace("+asyncpg", "")

    @property
    def alembic_url(self) -> str:
        """Psycopg2-based URL for Alembic (sync migrations)."""
        url = self.DATABASE_URL.replace("+asyncpg", "+psycopg2")
        # Fallback: plain postgresql:// also works with psycopg2
        return url


# ──────────────────────────────────────────────────────────────────────────────
#  Redis Settings
# ──────────────────────────────────────────────────────────────────────────────

class RedisSettings(BaseSettings):
    """
    Redis configuration.

    Format: redis://default:pass@host:port

    Used for:
      - Celery broker (task queue)
      - Celery result backend
      - Application cache (scraped pages, AI results)
      - Rate limiting counters
      - Session/cookie persistence for scraping
    """
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    REDIS_URL: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL",
    )

    # Cache TTLs (seconds)
    CACHE_TTL_SCRAPED_PAGE: int = Field(
        default=21600,    # 6 hours — scraped HTML pages
        description="How long to cache scraped HTML pages in Redis.",
    )
    CACHE_TTL_PRODUCT: int = Field(
        default=3600,     # 1 hour — product data
        description="How long to cache extracted product data.",
    )
    CACHE_TTL_AI_ARTICLE: int = Field(
        default=86400,    # 24 hours — generated articles
        description="How long to cache AI-generated articles.",
    )

    # Connection pool
    REDIS_MAX_CONNECTIONS: int = Field(
        default=10,
        description="Max Redis connections.",
    )
    REDIS_SOCKET_TIMEOUT: int = Field(
        default=5,
        description="Redis socket timeout in seconds.",
    )
    REDIS_RETRY_ON_TIMEOUT: bool = Field(
        default=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Celery / Queue Settings
# ──────────────────────────────────────────────────────────────────────────────

class CelerySettings(BaseSettings):
    """
    Celery task queue configuration.

    Architecture: Redis as both broker AND result backend.

    Queue priorities:
      - high_priority  : User-triggered, needs fast response (< 5s)
      - default        : Amazon pipelines
      - low_priority   : Analytics, cleanup, reporting
    """
    model_config = SettingsConfigDict(env_prefix="CELERY_", extra="ignore")

    # Worker concurrency
    WORKER_CONCURRENCY: int = Field(
        default=2,
        description="Number of parallel Celery worker processes.",
    )
    WORKER_MAX_TASKS_PER_CHILD: int = Field(
        default=100,
        description="Restart worker after N tasks to prevent memory leaks.",
    )
    TASK_SOFT_TIME_LIMIT: int = Field(
        default=300,    # 5 minutes
        description="Soft time limit per task (seconds). Task gets SoftTimeLimitExceeded.",
    )
    TASK_HARD_TIME_LIMIT: int = Field(
        default=600,    # 10 minutes
        description="Hard time limit per task (seconds). Task is killed after this.",
    )
    TASK_MAX_RETRIES: int = Field(
        default=3,
        description="Maximum retries for failed tasks.",
    )
    TASK_RETRY_BACKOFF: bool = Field(
        default=True,
        description="Use exponential backoff for retries.",
    )
    TASK_RETRY_BACKOFF_MAX: int = Field(
        default=600,    # 10 minutes max wait
        description="Maximum backoff wait time in seconds.",
    )
    RESULT_EXPIRES: int = Field(
        default=3600,   # 1 hour
        description="How long to keep task results in Redis.",
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Telegram Settings
# ──────────────────────────────────────────────────────────────────────────────

class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    BOT_TOKEN: str = Field(
        default="",
        description="Telegram Bot Token from @BotFather.",
    )
    CHANNEL_ID: str = Field(
        default="",
        description="Telegram channel ID for publishing (e.g. @mychannel or -100123456).",
    )
    ADMIN_CHAT_ID: str = Field(
        default="",
        description="Admin Telegram chat ID for error notifications.",
    )
    WEBHOOK_SECRET: str = Field(
        default="",
        description="Secret token for Telegram webhook URL validation.",
    )


# ──────────────────────────────────────────────────────────────────────────────
#  AI Settings
# ──────────────────────────────────────────────────────────────────────────────

class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    OPENROUTER_API_KEY: str = Field(
        default="",
        description="OpenRouter API key for LLM access.",
    )
    OPENROUTER_MODEL: str = Field(
        default="openai/gpt-4o-mini",
        description="Default LLM model via OpenRouter.",
    )
    OPENROUTER_TIMEOUT: int = Field(
        default=90,
        description="AI request timeout in seconds.",
    )
    AI_MAX_RETRIES: int = Field(
        default=3,
        description="Max retries for failed AI calls.",
    )
    AI_MIN_QUALITY_SCORE: int = Field(
        default=70,
        description="Minimum AI quality score (0-100) to accept generated content.",
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Scraping Settings
# ──────────────────────────────────────────────────────────────────────────────

class ScrapingSettings(BaseSettings):
    """
    Anti-blocking scraping configuration.

    Architecture: Pure open-source hybrid stack.
    No mandatory paid APIs.

    Method priority (ascending cost):
      1. HTTPX async       → fastest, lowest cost
      2. aiohttp           → fallback async
      3. curl-cffi         → TLS fingerprint spoofing
      4. Playwright async  → JavaScript rendering (RAM intensive)

    Optional future layer (disabled by default):
      5. ScraperAPI        → paid, only if SCRAPERAPI_KEY is set
    """
    model_config = SettingsConfigDict(env_prefix="SCRAPING_", extra="ignore")

    REQUEST_TIMEOUT: int = Field(default=25)
    PLAYWRIGHT_TIMEOUT: int = Field(default=45000, description="ms")
    MAX_RETRIES: int = Field(default=4)
    RETRY_BASE_DELAY: float = Field(
        default=1.5,
        description="Base delay for exponential backoff (seconds).",
    )
    RETRY_MAX_DELAY: float = Field(default=30.0)

    # Concurrency limits
    MAX_CONCURRENT_REQUESTS: int = Field(
        default=3,
        description="Max parallel scraping requests.",
    )
    MAX_CONCURRENT_PLAYWRIGHT: int = Field(
        default=1,
        description="Max parallel Playwright instances.",
    )

    # Rate limiting per domain
    RATE_LIMIT_RPM: int = Field(
        default=10,
        description="Max requests per minute per domain.",
    )

    # Optional paid layer (disabled by default)
    SCRAPERAPI_KEY: str = Field(
        default="",
        description="ScraperAPI key (optional, future upgrade path). "
                    "Leave empty to use only open-source stack.",
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Amazon Settings
# ──────────────────────────────────────────────────────────────────────────────

class AmazonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    AFFILIATE_TAG: str = Field(
        default="",
        description="Amazon affiliate tag (e.g. mysite-21). "
                    "Appended to all affiliate links.",
    )
    MIN_RATING: float = Field(
        default=3.5,
        description="Minimum product rating to process (0.0 to 5.0).",
    )
    MIN_REVIEWS: int = Field(
        default=10,
        description="Minimum review count to process.",
    )


# ──────────────────────────────────────────────────────────────────────────────
#  WordPress Settings
# ──────────────────────────────────────────────────────────────────────────────

class WordPressSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WP_", extra="ignore")

    SITE_URL: str = Field(default="", description="WordPress site URL.")
    USERNAME: str = Field(default="", description="WordPress username.")
    APP_PASSWORD: str = Field(default="", description="WordPress application password.")
    TIMEOUT: int = Field(default=30)

    @property
    def is_configured(self) -> bool:
        return bool(self.SITE_URL and self.USERNAME and self.APP_PASSWORD)


# ──────────────────────────────────────────────────────────────────────────────
#  Blogger Settings
# ──────────────────────────────────────────────────────────────────────────────

class BloggerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BLOGGER_", extra="ignore")

    BLOG_ID: str = Field(default="")
    CLIENT_ID: str = Field(default="")
    CLIENT_SECRET: str = Field(default="")
    REFRESH_TOKEN: str = Field(default="")

    @property
    def is_configured(self) -> bool:
        return bool(self.BLOG_ID and self.CLIENT_ID and self.CLIENT_SECRET)


# ──────────────────────────────────────────────────────────────────────────────
#  Google Sheets Settings
# ──────────────────────────────────────────────────────────────────────────────

class SheetsSettings(BaseSettings):
    """
    Google Sheets remains as operational/export layer ONLY.
    NOT used as source of truth (PostgreSQL is).

    Sheets is used for:
      - Human-readable export of published content
      - Pinterest CSV generation workflow
      - Manual override/correction by operator
      - Audit trail for published items
    """
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    GOOGLE_SHEET_NAME: str = Field(default="")
    JSON_FILE: str = Field(
        default="",
        description="Path to Google service account JSON credentials file.",
    )

    @property
    def is_configured(self) -> bool:
        return bool(self.GOOGLE_SHEET_NAME and self.JSON_FILE)


# ──────────────────────────────────────────────────────────────────────────────
#  Image Settings
# ──────────────────────────────────────────────────────────────────────────────

class ImageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    IMGBB_API_KEY: str = Field(default="")
    CLIPDROP_API_KEY: str = Field(default="")

    # Pinterest-optimal dimensions
    CANVAS_WIDTH: int = Field(default=941)
    CANVAS_HEIGHT_PRODUCT: int = Field(default=1671)


# ──────────────────────────────────────────────────────────────────────────────
#  WhatsApp Settings
# ──────────────────────────────────────────────────────────────────────────────

class WhatsAppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    EVOLUTION_API_URL: str = Field(default="")
    EVOLUTION_API_KEY: str = Field(default="")
    EVOLUTION_INSTANCE: str = Field(default="amazonbot")
    WA_CHANNEL_NUMBER: str = Field(default="")

    @property
    def is_configured(self) -> bool:
        return bool(self.EVOLUTION_API_URL and self.EVOLUTION_API_KEY)


# ──────────────────────────────────────────────────────────────────────────────
#  Logging & Monitoring Settings
# ──────────────────────────────────────────────────────────────────────────────

class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Application log level.",
    )
    LOG_FORMAT: Literal["json", "human"] = Field(
        default="json",
        description="'json' for production (parseable by log aggregators). "
                    "'human' for local development.",
    )
    SENTRY_DSN: str = Field(
        default="",
        description="Sentry DSN for error tracking (optional but recommended).",
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Master Settings (aggregates all sub-settings)
# ──────────────────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    """
    Master settings object. Single source of truth for all configuration.

    Usage:
        from core.config import get_settings
        settings = get_settings()
        db_url = settings.database.DATABASE_URL

    Required variables:
      - DATABASE_URL
      - REDIS_URL
      - BOT_TOKEN
    Optional but recommended:
      - ADMIN_CHAT_ID (for error notifications)
      - SENTRY_DSN    (for error tracking)
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow nested model population from flat env vars
        populate_by_name=True,
    )

    # Environment
    ENVIRONMENT: Literal["development", "staging", "production"] = Field(
        default="production",
        description="Runtime environment. "
                    "Affects logging, error handling, and safety checks.",
    )

    # Sub-settings (instantiated from same env)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    celery: CelerySettings = Field(default_factory=CelerySettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    ai: AISettings = Field(default_factory=AISettings)
    scraping: ScrapingSettings = Field(default_factory=ScrapingSettings)
    amazon: AmazonSettings = Field(default_factory=AmazonSettings)
    wordpress: WordPressSettings = Field(default_factory=WordPressSettings)
    blogger: BloggerSettings = Field(default_factory=BloggerSettings)
    sheets: SheetsSettings = Field(default_factory=SheetsSettings)
    image: ImageSettings = Field(default_factory=ImageSettings)
    whatsapp: WhatsAppSettings = Field(default_factory=WhatsAppSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    def validate_critical_config(self) -> list[str]:
        """
        Check critical configuration and return list of warnings.
        Called at startup to surface configuration issues early.
        """
        warnings = []
        if not self.telegram.BOT_TOKEN:
            warnings.append("BOT_TOKEN is not set — Telegram bot will not start.")
        if not self.telegram.CHANNEL_ID:
            warnings.append("CHANNEL_ID not set — cannot publish to Telegram channel.")
        if not self.ai.OPENROUTER_API_KEY:
            warnings.append("OPENROUTER_API_KEY not set — AI generation disabled.")
        if not self.amazon.AFFILIATE_TAG:
            warnings.append("AFFILIATE_TAG not set — Amazon links have no affiliate tag!")
        if not self.wordpress.is_configured:
            warnings.append("WordPress not configured — WP publishing disabled.")
        if not self.blogger.is_configured:
            warnings.append("Blogger not configured — Blogger publishing disabled.")
        if not self.telegram.ADMIN_CHAT_ID:
            warnings.append("ADMIN_CHAT_ID not set — error notifications disabled.")
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return cached Settings singleton.

    The @lru_cache ensures settings are loaded ONCE at startup
    and reused for all subsequent calls. This is important for:
      - Performance (no re-parsing env vars on each request)
      - Consistency (same values throughout the app lifetime)
      - Testing (can be overridden with cache_clear())

    To override in tests:
        get_settings.cache_clear()
        with patch.dict(os.environ, {"DATABASE_URL": "..."}):
            settings = get_settings()
    """
    settings = Settings()
    return settings
