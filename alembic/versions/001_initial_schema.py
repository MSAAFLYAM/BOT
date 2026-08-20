"""Initial schema — Products, Jobs, Analytics

Revision ID: 001
Revises: None
Create Date: 2026-05-25

This migration creates the complete initial database schema.

Tables created:
  - products       : Amazon product lifecycle
  - jobs           : Async job tracking (replaces in-memory state)
  - analytics_events : Append-only event log

Rollback strategy:
  alembic downgrade -1

  Drops all tables, all types, all indexes.
  DESTRUCTIVE — use with care in production.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Revision identifiers
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Create custom ENUM types ──────────────────────────────────────────────
    # PostgreSQL native ENUMs are more efficient than VARCHAR with CHECK.
    # They are created once and reused across tables.

    publish_status = postgresql.ENUM(
        "pending", "running", "published", "failed", "skipped",
        name="publish_status_enum",
        create_type=True,
    )
    publish_status.create(op.get_bind(), checkfirst=True)

    product_status = postgresql.ENUM(
        "new", "processing", "ready", "published", "failed", "rejected",
        name="product_status_enum",
        create_type=True,
    )
    product_status.create(op.get_bind(), checkfirst=True)

    approval_status = postgresql.ENUM(
        "pending_review", "approved", "rejected", "auto_approved",
        name="approval_status_enum",
        create_type=True,
    )
    approval_status.create(op.get_bind(), checkfirst=True)

    job_type = postgresql.ENUM(
        "amazon_scrape", "amazon_ai", "amazon_image", "amazon_publish", "amazon_full",
        "publish_wordpress", "publish_blogger", "publish_telegram",
        "publish_pinterest", "publish_whatsapp",
        "sheets_sync", "pinterest_export", "image_cleanup",
        name="job_type_enum",
        create_type=True,
    )
    job_type.create(op.get_bind(), checkfirst=True)

    job_status = postgresql.ENUM(
        "pending", "running", "success", "failed", "retrying",
        "exhausted", "cancelled", "awaiting_approval",
        name="job_status_enum",
        create_type=True,
    )
    job_status.create(op.get_bind(), checkfirst=True)

    entity_type = postgresql.ENUM(
        "product",
        name="entity_type_enum",
        create_type=True,
    )
    entity_type.create(op.get_bind(), checkfirst=True)

    event_type = postgresql.ENUM(
        "published", "publish_failed", "publish_retry",
        "scraped", "scrape_failed", "ai_generated", "ai_failed",
        "image_processed", "image_failed",
        "approved", "rejected", "submitted",
        name="event_type_enum",
        create_type=True,
    )
    event_type.create(op.get_bind(), checkfirst=True)

    # ── Create products table ─────────────────────────────────────────────────

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asin", sa.String(20), nullable=False),
        sa.Column("marketplace", sa.String(20), nullable=False, server_default="amazon.fr"),
        sa.Column("affiliate_link", sa.String(1000), nullable=True),
        sa.Column("original_url", sa.String(1000), nullable=True),
        sa.Column("title", sa.String(1000), nullable=True),
        sa.Column("short_description", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(200), nullable=True),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=True),
        sa.Column("old_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("discount_pct", sa.SmallInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("rating", sa.Numeric(3, 1), nullable=True),
        sa.Column("reviews_count", sa.Integer(), nullable=True),
        sa.Column("is_bestseller", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("bestseller_rank", sa.String(200), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("coupon_text", sa.String(200), nullable=True),
        sa.Column("image_url", sa.String(1000), nullable=True),
        sa.Column("image_hosted_url", sa.String(1000), nullable=True),
        sa.Column("canvas_url", sa.String(1000), nullable=True),
        sa.Column("seo_title", sa.String(200), nullable=True),
        sa.Column("seo_description", sa.String(300), nullable=True),
        sa.Column("html_article", sa.Text(), nullable=True),
        sa.Column("keywords", sa.String(500), nullable=True),
        sa.Column("ai_quality_score", sa.SmallInteger(), nullable=True),
        sa.Column("seo_score", sa.SmallInteger(), nullable=True),
        # Status columns
        sa.Column("status", sa.Enum("product_status_enum", create_constraint=False),
                  nullable=False, server_default="new"),
        sa.Column("telegram_status", sa.Enum("publish_status_enum", create_constraint=False),
                  nullable=False, server_default="pending"),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("wp_status", sa.Enum("publish_status_enum", create_constraint=False),
                  nullable=False, server_default="pending"),
        sa.Column("wp_post_id", sa.Integer(), nullable=True),
        sa.Column("wp_post_url", sa.String(500), nullable=True),
        sa.Column("blogger_status", sa.Enum("publish_status_enum", create_constraint=False),
                  nullable=False, server_default="pending"),
        sa.Column("blogger_post_id", sa.String(100), nullable=True),
        sa.Column("blogger_post_url", sa.String(500), nullable=True),
        sa.Column("pinterest_status", sa.Enum("publish_status_enum", create_constraint=False),
                  nullable=False, server_default="pending"),
        sa.Column("pinterest_pin_id", sa.String(100), nullable=True),
        sa.Column("pinterest_board", sa.String(200), nullable=True),
        sa.Column("pinterest_scheduled_at", sa.String(50), nullable=True),
        sa.Column("whatsapp_status", sa.Enum("publish_status_enum", create_constraint=False),
                  nullable=False, server_default="pending"),
        sa.Column("best_url", sa.String(500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scrape_method", sa.String(50), nullable=True),
        sa.Column("sheets_row", sa.Integer(), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_asin",             "products", ["asin"],     unique=True)
    op.create_index("ix_products_status",           "products", ["status"])
    op.create_index("ix_products_pinterest_status", "products", ["pinterest_status"])
    op.create_index("ix_products_status_wp",        "products", ["status", "wp_status"])
    op.create_index("ix_products_status_pinterest", "products", ["status", "pinterest_status"])
    op.create_index("ix_products_created_at",       "products", ["created_at"])

    # ── Create jobs table ─────────────────────────────────────────────────────

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("type", sa.Enum("job_type_enum", create_constraint=False),
                  nullable=False),
        sa.Column("status", sa.Enum("job_status_enum", create_constraint=False),
                  nullable=False, server_default="pending"),
        sa.Column("celery_task_id", sa.String(200), nullable=True),
        sa.Column("queue_name", sa.String(50), nullable=False, server_default="default"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column("error_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("retry_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.SmallInteger(), nullable=False, server_default="3"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("chat_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="5"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_type",          "jobs", ["type"])
    op.create_index("ix_jobs_status",        "jobs", ["status"])
    op.create_index("ix_jobs_type_status",   "jobs", ["type", "status"])
    op.create_index("ix_jobs_chat_status",   "jobs", ["chat_id", "status"])
    op.create_index("ix_jobs_product_type",  "jobs", ["product_id", "type"])
    op.create_index("ix_jobs_error_type",    "jobs", ["error_type", "status"])
    op.create_index("ix_jobs_scheduled_for", "jobs", ["scheduled_for", "status"])
    op.create_index("ix_jobs_celery_id",     "jobs", ["celery_task_id"])
    op.create_index("ix_jobs_created_at",    "jobs", ["created_at"])

    # ── Create analytics_events table ─────────────────────────────────────────

    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.Enum("entity_type_enum", create_constraint=False),
                  nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column("event_type", sa.Enum("event_type_enum", create_constraint=False),
                  nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_entity",       "analytics_events", ["entity_type", "entity_id"])
    op.create_index("ix_analytics_platform_date","analytics_events", ["platform", "recorded_at"])
    op.create_index("ix_analytics_event_date",   "analytics_events", ["event_type", "recorded_at"])
    op.create_index("ix_analytics_recorded_at",  "analytics_events", ["recorded_at"])


def downgrade() -> None:
    """
    Rollback: drop all tables and enum types.

    DESTRUCTIVE — use only in development or emergency rollback.
    In production, always test migrations on staging first.
    """
    # Drop tables (reverse creation order to respect dependencies)
    op.drop_table("analytics_events")
    op.drop_table("jobs")
    op.drop_table("products")

    # Drop enum types
    for enum_name in [
        "publish_status_enum",
        "product_status_enum",
        "approval_status_enum",
        "job_type_enum",
        "job_status_enum",
        "entity_type_enum",
        "event_type_enum",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
