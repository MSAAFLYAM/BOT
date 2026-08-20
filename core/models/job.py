"""
core/models/job.py — Persistent job tracking model.

Architecture decisions:
  - UUID primary key: matches Celery task IDs format.
    Allows direct lookup from Celery result backend.
  - payload stored as JSONB: flexible, no schema migration for new job types.
  - result stored as JSONB: same flexibility for varying outputs.
  - error_detail stored as JSONB: structured error context (not just string).
    Allows querying "all jobs that failed with ScrapingBlockedError".
  - Separate max_retries per job: different jobs have different retry budgets.
  - next_retry_at enables "retry after N seconds" without Celery ETA complexity.
  - chat_id/message_id: allows updating the Telegram message after job completes.

Why this replaces _pending_items:
  - _pending_items: in-memory dict, lost on restart, unqueryable.
  - Job model: PostgreSQL, survives restarts, fully queryable, auditable.

Scalability:
  - Index on (type, status) for worker job polling.
  - Index on next_retry_at for retry scheduler.
  - Index on (chat_id, status) for Telegram notification lookups.
  - Celery task ID index for cross-referencing with Celery result backend.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, Index, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class JobType(str, enum.Enum):
    """All possible job types in the system."""
    # Amazon pipeline
    AMAZON_SCRAPE     = "amazon_scrape"
    AMAZON_AI         = "amazon_ai"
    AMAZON_IMAGE      = "amazon_image"
    AMAZON_PUBLISH    = "amazon_publish"
    AMAZON_FULL       = "amazon_full"       # Complete pipeline

    # Publishing (per platform)
    PUBLISH_WORDPRESS = "publish_wordpress"
    PUBLISH_BLOGGER   = "publish_blogger"
    PUBLISH_TELEGRAM  = "publish_telegram"
    PUBLISH_PINTEREST = "publish_pinterest"
    PUBLISH_WHATSAPP  = "publish_whatsapp"

    # Maintenance
    SHEETS_SYNC       = "sheets_sync"
    PINTEREST_EXPORT  = "pinterest_export"
    IMAGE_CLEANUP     = "image_cleanup"


class JobStatus(str, enum.Enum):
    """Job lifecycle states."""
    PENDING   = "pending"    # Created, queued
    RUNNING   = "running"    # Worker picked it up
    SUCCESS   = "success"    # Completed successfully
    FAILED    = "failed"     # Failed, may be retried
    RETRYING  = "retrying"   # Scheduled for retry
    EXHAUSTED = "exhausted"  # Max retries reached, giving up
    CANCELLED = "cancelled"  # Manually cancelled
    # Special states
    AWAITING_APPROVAL = "awaiting_approval"  # Human must approve via Telegram


class Job(Base, UUIDMixin, TimestampMixin):
    """
    Persistent job tracking. Every pipeline operation is a Job.

    Table: jobs

    This is the single source of truth for all async operations.
    Replaces the volatile in-memory state that was lost on restart.
    """
    __tablename__ = "jobs"

    # ── Job Identity ─────────────────────────────────────────────────────────

    type: Mapped[JobType] = mapped_column(
        Enum(JobType, name="job_type_enum"),
        nullable=False,
        index=True,
        doc="Job type determines which Celery task handles it.",
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status_enum"),
        default=JobStatus.PENDING,
        nullable=False,
        index=True,
    )

    # ── Celery Integration ────────────────────────────────────────────────────

    celery_task_id: Mapped[Optional[str]] = mapped_column(
        String(200),
        index=True,
        doc="Celery task UUID. Allows cross-referencing with Redis result backend.",
    )
    queue_name: Mapped[str] = mapped_column(
        String(50),
        default="default",
        doc="Which Celery queue this job was sent to.",
    )

    # ── Payload & Result ──────────────────────────────────────────────────────

    payload: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        doc="Input data for the job. Schema varies by job type. "
            "Examples: {'url': '...', 'asin': '...'} for amazon_scrape. "
            "{'product_id': 5, 'chat_id': 123} for product_approval.",
    )
    result: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        doc="Output data after successful completion. "
            "Examples: {'product_id': 42, 'wp_url': '...'} for amazon_full.",
    )

    # ── Error Handling ────────────────────────────────────────────────────────

    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        doc="Human-readable error message from last failure.",
    )
    error_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        index=True,
        doc="Exception class name (e.g. 'ScrapingBlockedError'). "
            "Indexed for analytics queries like 'how many blocking errors today?'",
    )
    error_detail: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        doc="Structured error context from exception.to_dict(). "
            "Allows querying failed jobs by error type and context.",
    )

    # ── Retry Configuration ───────────────────────────────────────────────────

    retry_count: Mapped[int] = mapped_column(
        SmallInteger,
        default=0,
        doc="Number of times this job has been retried.",
    )
    max_retries: Mapped[int] = mapped_column(
        SmallInteger,
        default=3,
        doc="Maximum retries before status becomes EXHAUSTED.",
    )
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        index=True,
        doc="UTC datetime for next retry attempt. "
            "Used by retry scheduler to pick up jobs.",
    )

    # ── Entity References ─────────────────────────────────────────────────────

    product_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        index=True,
        doc="Associated Product ID (if job is for a product).",
    )


    # ── Telegram Context ──────────────────────────────────────────────────────

    chat_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        index=True,
        doc="Telegram chat ID that triggered this job. "
            "Used to notify user of completion/failure.",
    )
    message_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        doc="Telegram message ID of the job status message. "
            "Used to update the message when job completes.",
    )

    # ── Timing ────────────────────────────────────────────────────────────────

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        doc="When the worker picked up this job.",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        doc="When the job finished (success or final failure).",
    )

    # ── Scheduling ────────────────────────────────────────────────────────────

    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        index=True,
        doc="For scheduled jobs. Worker won't pick up until this time.",
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger,
        default=5,
        doc="Job priority (1=highest, 10=lowest). "
            "Used by worker to order the queue.",
    )

    # ── Indexes ───────────────────────────────────────────────────────────────

    __table_args__ = (
        Index("ix_jobs_type_status",          "type", "status"),
        Index("ix_jobs_chat_status",          "chat_id", "status"),
        Index("ix_jobs_product_type",         "product_id", "type"),

        Index("ix_jobs_error_type",           "error_type", "status"),
        Index("ix_jobs_scheduled_for",        "scheduled_for", "status"),
        Index("ix_jobs_created_at",           "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Job id={str(self.id)[:8]}... type={self.type.value!r} "
            f"status={self.status.value!r} retries={self.retry_count}>"
        )

    @property
    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def is_terminal(self) -> bool:
        """True if job cannot change status further."""
        return self.status in (
            JobStatus.SUCCESS,
            JobStatus.EXHAUSTED,
            JobStatus.CANCELLED,
        )
