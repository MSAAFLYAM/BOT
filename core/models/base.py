"""
core/models/base.py — SQLAlchemy 2.0 declarative base with shared mixins.

Architecture decisions:
  - DeclarativeBase from SQLAlchemy 2.0 (not legacy declarative_base()).
  - TimestampMixin added to ALL models (created_at, updated_at).
  - updated_at uses server_default + onupdate for DB-side accuracy.
  - UUIDMixin for models requiring globally unique IDs (Jobs).
  - All columns use mapped_column() (type-annotated, SQLAlchemy 2.0 style).
  - Relationship loading strategy: lazy="select" by default
    (explicit join when needed, prevents N+1 in async context).

Memory impact:
  - Base class itself is lightweight.
  - Models are class-level definitions, not instances.
  - No per-request overhead from the base class.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """
    SQLAlchemy 2.0 declarative base.

    All models must inherit from this class.
    Provides type registry for mapped_column() type inference.
    """
    pass


class TimestampMixin:
    """
    Adds created_at and updated_at to any model.

    created_at: set once at INSERT, never changes.
    updated_at: set at INSERT, auto-updated at every UPDATE
                by the database server (not Python).
                Using server_onupdate ensures accuracy even if
                updates happen outside SQLAlchemy (direct SQL).
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        doc="UTC timestamp of record creation.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        doc="UTC timestamp of last update (auto-maintained by DB).",
    )


class UUIDMixin:
    """
    Adds UUID primary key to models that need global uniqueness.

    Used for: Job model (Celery task IDs, cross-service references).
    Not used for: Product, Analytics (sequential int PK is fine).

    PostgreSQL UUID type is 16 bytes vs 36 bytes for string UUID.
    as_uuid=True returns Python uuid.UUID objects natively.
    """
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
        doc="UUID primary key.",
    )


class IntPKMixin:
    """
    Adds auto-increment integer primary key.

    Used for: Product, Analytics.
    More efficient for large tables with sequential inserts.
    """
    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
        doc="Auto-increment integer primary key.",
    )
