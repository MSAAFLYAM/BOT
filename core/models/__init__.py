"""
core/models/__init__.py — Model exports.

Import all models here so Alembic can discover them via:
  from core.models import *  (in alembic/env.py)

Order matters for foreign key resolution:
  Base models first, then models with FK dependencies.
"""
from .base import Base, IntPKMixin, TimestampMixin, UUIDMixin
from .product import Product, PublishStatus, ProductStatus
from .job import Job, JobType, JobStatus
from .analytics import AnalyticsEvent, EntityType, Platform, EventType

__all__ = [
    # Base
    "Base",
    "IntPKMixin",
    "TimestampMixin",
    "UUIDMixin",
    # Product
    "Product",
    "PublishStatus",
    "ProductStatus",
    # Job
    "Job",
    "JobType",
    "JobStatus",
    # Analytics
    "AnalyticsEvent",
    "EntityType",
    "Platform",
    "EventType",
]
