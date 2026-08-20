"""
core/models/analytics.py — Analytics tracking model.

Architecture decisions:
  - Append-only events table (no updates, no deletes).
    Append-only is simpler to reason about and easier to partition.
  - JSONB data field for flexible event metadata.
  - Separate entity_type + entity_id instead of polymorphic FK.
    Avoids complex JOIN logic for multi-entity analytics.
  - recorded_at is the event timestamp (not created_at from TimestampMixin).
    TimestampMixin not used here — analytics records never "update".

Scalability:
  - This table will grow fast (every publish event = 1 row).
  - Partitioning by recorded_at recommended for >1M rows.
  - GIN index on data for metadata queries.
  - Index on (entity_type, entity_id) for per-entity analytics.
  - Index on (platform, recorded_at) for platform-wide reports.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IntPKMixin


class EntityType(str, enum.Enum):
    PRODUCT  = "product"


class Platform(str, enum.Enum):
    TELEGRAM  = "telegram"
    WORDPRESS = "wordpress"
    BLOGGER   = "blogger"
    PINTEREST = "pinterest"
    WHATSAPP  = "whatsapp"


class EventType(str, enum.Enum):
    # Publishing events
    PUBLISHED      = "published"
    PUBLISH_FAILED = "publish_failed"
    PUBLISH_RETRY  = "publish_retry"

    # Pipeline events
    SCRAPED        = "scraped"
    SCRAPE_FAILED  = "scrape_failed"
    AI_GENERATED   = "ai_generated"
    AI_FAILED      = "ai_failed"
    IMAGE_PROCESSED = "image_processed"
    IMAGE_FAILED   = "image_failed"

    # User events
    APPROVED       = "approved"
    REJECTED       = "rejected"
    SUBMITTED      = "submitted"


class AnalyticsEvent(Base, IntPKMixin):
    """
    Immutable analytics event log.

    Table: analytics_events

    Records every significant event in the pipeline.
    Used for monitoring, reporting, and performance optimization.
    """
    __tablename__ = "analytics_events"

    # ── Event Classification ─────────────────────────────────────────────────

    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType, name="entity_type_enum"),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        doc="Product.id.",
    )
    platform: Mapped[Optional[str]] = mapped_column(
        String(50),
        index=True,
        doc="Which platform this event relates to (nullable for pipeline events).",
    )
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type_enum"),
        nullable=False,
        index=True,
    )

    # ── Event Metadata ────────────────────────────────────────────────────────

    data: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        doc="Event-specific metadata. "
            "Examples: {'url': '...', 'duration_ms': 1234} for PUBLISHED. "
            "{'error': '...', 'method': 'playwright'} for SCRAPE_FAILED.",
    )

    # ── Timing ────────────────────────────────────────────────────────────────

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        doc="When this event occurred (UTC). "
            "This is the authoritative timestamp for analytics.",
    )

    # ── Indexes ───────────────────────────────────────────────────────────────

    __table_args__ = (
        Index("ix_analytics_entity",          "entity_type", "entity_id"),
        Index("ix_analytics_platform_date",   "platform", "recorded_at"),
        Index("ix_analytics_event_date",      "event_type", "recorded_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AnalyticsEvent {self.event_type.value!r} "
            f"entity={self.entity_type.value}/{self.entity_id} "
            f"platform={self.platform!r}>"
        )
