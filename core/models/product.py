"""
core/models/product.py — Amazon Product ORM model.

Architecture decisions:
  - ASIN is the natural unique key (Amazon's product identifier).
  - Separate image columns: original (Amazon CDN) vs hosted (our CDN).
    Never use Amazon CDN URLs in Pinterest — they return 403.
  - Publishing status per platform as enum columns.
    This allows querying "all products pending for Pinterest" efficiently.
  - html_article stored as Text (not JSON) — full HTML, can be large.
  - seo_title/seo_description stored separately from full article
    for use in social posts without loading the full article.
  - ai_quality_score and seo_score are computed and stored,
    allowing filtering by quality without re-running AI.
  - best_url: after WP + Blogger publish, we pick the better URL.
    This URL is used in Pinterest pins.
  - sheets_row: optional sync reference to Google Sheets row.
    Allows bidirectional sync without full sheet scan.

Scalability:
  - Index on asin (unique lookups by URL parsing).
  - Index on (wp_status, blogger_status) for batch publishing queries.
  - Index on created_at for time-based analytics.
  - Index on scheduled_at for scheduler queries.
"""
from __future__ import annotations

import enum
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, Enum, Index, Integer, Numeric, SmallInteger, String, Text
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IntPKMixin, TimestampMixin


class PublishStatus(str, enum.Enum):
    """
    Publishing status for each platform.

    Using str enum ensures values are stored as readable strings
    in PostgreSQL (not integers), making the DB human-readable.
    """
    PENDING   = "pending"    # Queued but not yet processed
    RUNNING   = "running"    # Currently being published
    PUBLISHED = "published"  # Successfully published
    FAILED    = "failed"     # Failed (check error_message)
    SKIPPED   = "skipped"    # Intentionally skipped (manual override)


class ProductStatus(str, enum.Enum):
    """Overall product workflow status."""
    NEW        = "new"        # Just extracted, not yet processed
    PROCESSING = "processing" # AI/image pipeline running
    READY      = "ready"      # Ready to publish
    PUBLISHED  = "published"  # All platforms published
    FAILED     = "failed"     # Pipeline failed
    REJECTED   = "rejected"   # Failed quality filters


class Product(Base, IntPKMixin, TimestampMixin):
    """
    Amazon product with complete lifecycle tracking.

    Table: products
    """
    __tablename__ = "products"

    # ── Amazon Identity ──────────────────────────────────────────────────────

    asin: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        index=True,
        nullable=False,
        doc="Amazon Standard Identification Number. Natural unique key.",
    )
    marketplace: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="amazon.fr",
        doc="Amazon marketplace (amazon.fr, amazon.com, etc.).",
    )
    affiliate_link: Mapped[Optional[str]] = mapped_column(
        String(1000),
        doc="Full affiliate URL with tag parameter.",
    )
    original_url: Mapped[Optional[str]] = mapped_column(
        String(1000),
        doc="Original URL submitted by user (may be shortened or tracking URL).",
    )

    # ── Product Data ─────────────────────────────────────────────────────────

    title: Mapped[Optional[str]] = mapped_column(
        String(1000),
        doc="Full product title from Amazon.",
    )
    short_description: Mapped[Optional[str]] = mapped_column(
        Text,
        doc="Short product description/bullet points.",
    )
    brand: Mapped[Optional[str]] = mapped_column(String(200))
    category: Mapped[Optional[str]] = mapped_column(String(200))

    price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2),
        doc="Current price in local currency.",
    )
    old_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2),
        doc="Original price before discount.",
    )
    discount_pct: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        doc="Discount percentage (0-100).",
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        default="EUR",
        doc="ISO 4217 currency code.",
    )

    rating: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(3, 1),
        doc="Product rating (0.0 to 5.0).",
    )
    reviews_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        doc="Number of reviews.",
    )
    is_bestseller: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        doc="True if product has a #1 Bestseller badge.",
    )
    bestseller_rank: Mapped[Optional[str]] = mapped_column(
        String(200),
        doc="Bestseller rank string (e.g. '#1 in Kitchen & Dining').",
    )
    is_available: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        doc="False if product is out of stock.",
    )
    coupon_text: Mapped[Optional[str]] = mapped_column(
        String(200),
        doc="Coupon/promo code or discount badge text.",
    )

    # ── Images ───────────────────────────────────────────────────────────────

    image_url: Mapped[Optional[str]] = mapped_column(
        String(1000),
        doc="Original Amazon CDN image URL. "
            "NEVER use this for Pinterest (returns 403). "
            "Use image_hosted_url instead.",
    )
    image_hosted_url: Mapped[Optional[str]] = mapped_column(
        String(1000),
        doc="Image re-hosted on our CDN (IMGBB, Catbox, etc.). "
            "Safe to use in Pinterest pins.",
    )
    canvas_url: Mapped[Optional[str]] = mapped_column(
        String(1000),
        doc="Pinterest-ready canvas card URL (hosted). "
            "Generated by the canvas renderer with template.png.",
    )

    # ── AI / SEO Content ─────────────────────────────────────────────────────

    seo_title: Mapped[Optional[str]] = mapped_column(
        String(200),
        doc="SEO-optimized title (50-60 chars). Generated by AI.",
    )
    seo_description: Mapped[Optional[str]] = mapped_column(
        String(300),
        doc="Meta description (150-160 chars). Generated by AI.",
    )
    html_article: Mapped[Optional[str]] = mapped_column(
        Text,
        doc="Full HTML article. Can be 5000-15000 chars. "
            "Stored as Text, not loaded by default (use deferred loading).",
    )
    keywords: Mapped[Optional[str]] = mapped_column(
        String(500),
        doc="Comma-separated keywords for Pinterest and SEO.",
    )
    ai_quality_score: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        doc="AI content quality score (0-100). "
            "Computed by AI pipeline. Min threshold configurable.",
    )
    seo_score: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        doc="SEO score (0-100). Computed by SEO analyzer.",
    )

    # ── Publishing Status ─────────────────────────────────────────────────────

    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus, name="product_status_enum"),
        default=ProductStatus.NEW,
        nullable=False,
        index=True,
        doc="Overall workflow status.",
    )

    # Telegram
    telegram_status: Mapped[PublishStatus] = mapped_column(
        Enum(PublishStatus, name="publish_status_enum"),
        default=PublishStatus.PENDING,
        nullable=False,
        doc="Telegram channel publishing status.",
    )
    telegram_message_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        doc="Telegram message ID after successful publish.",
    )

    # WordPress
    wp_status: Mapped[PublishStatus] = mapped_column(
        Enum("publish_status_enum", create_constraint=False),
        default=PublishStatus.PENDING,
        nullable=False,
    )
    wp_post_id: Mapped[Optional[int]] = mapped_column(Integer)
    wp_post_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Blogger
    blogger_status: Mapped[PublishStatus] = mapped_column(
        Enum("publish_status_enum", create_constraint=False),
        default=PublishStatus.PENDING,
        nullable=False,
    )
    blogger_post_id: Mapped[Optional[str]] = mapped_column(String(100))
    blogger_post_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Pinterest
    pinterest_status: Mapped[PublishStatus] = mapped_column(
        Enum("publish_status_enum", create_constraint=False),
        default=PublishStatus.PENDING,
        nullable=False,
        index=True,
    )
    pinterest_pin_id: Mapped[Optional[str]] = mapped_column(String(100))
    pinterest_board: Mapped[Optional[str]] = mapped_column(String(200))
    pinterest_scheduled_at: Mapped[Optional[str]] = mapped_column(
        String(50),
        doc="ISO datetime for scheduled Pinterest pin.",
    )

    # WhatsApp
    whatsapp_status: Mapped[PublishStatus] = mapped_column(
        Enum("publish_status_enum", create_constraint=False),
        default=PublishStatus.PENDING,
        nullable=False,
    )

    # ── Workflow Metadata ─────────────────────────────────────────────────────

    best_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        doc="Best published article URL (WP or Blogger). "
            "Auto-selected after both platforms publish. "
            "Used in Pinterest pins for maximum SEO authority.",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        doc="Last error message if any pipeline step failed.",
    )
    error_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        doc="Exception type name (e.g. ScrapingBlockedError).",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        doc="Number of pipeline retry attempts.",
    )
    scrape_method: Mapped[Optional[str]] = mapped_column(
        String(50),
        doc="Which scraping method successfully extracted data.",
    )

    # Google Sheets sync reference (operational layer)
    sheets_row: Mapped[Optional[int]] = mapped_column(
        Integer,
        doc="Google Sheets row number for bidirectional sync. "
            "Sheets is NOT source of truth — this is a sync helper.",
    )

    # ── Indexes ───────────────────────────────────────────────────────────────

    __table_args__ = (
        Index("ix_products_status_wp",       "status", "wp_status"),
        Index("ix_products_status_pinterest", "status", "pinterest_status"),
        Index("ix_products_created_at",       "created_at"),
    )

    def __repr__(self) -> str:
        status_val = self.status.value if self.status else "None"
        return (
            f"<Product id={self.id} asin={self.asin!r} "
            f"status={status_val!r}>"
        )

    @property
    def has_hosted_image(self) -> bool:
        return bool(self.image_hosted_url)

    @property
    def has_canvas(self) -> bool:
        return bool(self.canvas_url)

    @property
    def is_pinterest_ready(self) -> bool:
        """Check if product can be exported to Pinterest CSV."""
        return (
            bool(self.title)
            and bool(self.canvas_url or self.image_hosted_url)
            and bool(self.best_url or self.affiliate_link)
            and self.pinterest_status == PublishStatus.PENDING
        )
