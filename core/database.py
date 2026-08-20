"""
core/database.py — Async PostgreSQL engine, session factory, and utilities.

Architecture decisions:
  - AsyncEngine with asyncpg driver (fastest async PostgreSQL driver).
  - async_sessionmaker creates sessions on demand (not a pool of sessions).
  - Connection pool settings calibrated for production use.
  - get_db() is an async context manager for dependency injection.
  - Separate engine for Alembic sync migrations (uses psycopg2).
  - Health check function for startup validation.
  - Repository pattern helpers (get_by_id, save, etc.) as standalone functions.
    Not methods on models — keeps models as pure data classes.

Connection Pool Strategy:
  - pool_size=5: max 5 persistent connections
  - max_overflow=2: up to 7 connections during spikes
  - pool_timeout=30: raise after 30s waiting for connection
  - pool_recycle=1800: recycle connections every 30 min
  - pool_pre_ping=True: check connection liveness before use

Memory impact:
  - Each connection uses ~8-16MB of RAM on the PostgreSQL side.
  - With pool_size=5: ~40-80MB for connections.

Async context:
  - All functions are async — never call from sync code without asyncio.run().
  - Use get_db() as async context manager in async handlers.
  - For Celery tasks: use asyncio.run(task_function()) in sync task wrapper.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import get_settings
from core.exceptions import DatabaseConnectionError

logger = logging.getLogger(__name__)

# ── Engine Singleton ──────────────────────────────────────────────────────────
# Created once at module import, reused for app lifetime.
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _get_engine() -> AsyncEngine:
    """
    Return the singleton async engine, creating it if needed.

    Not created at module import to support:
      - Testing (different DATABASE_URL per test)
      - Lazy initialization (some services don't need DB)
    """
    global _engine
    if _engine is None:
        import os as _os
        db_url = _os.environ.get("DATABASE_URL", "")
        if not db_url:
            try:
                db_url = get_settings().database.DATABASE_URL
            except Exception:
                db_url = "postgresql+asyncpg://localhost/amazonbot"
        # Convert postgres:// → postgresql+asyncpg://
        import re as _re
        db_url = _re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", db_url)
        if "+asyncpg" not in db_url and db_url.startswith("postgresql"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")

        _engine = create_async_engine(
            db_url,
            # Connection pool settings
            pool_size=db.DB_POOL_SIZE,
            max_overflow=db.DB_MAX_OVERFLOW,
            pool_timeout=db.DB_POOL_TIMEOUT,
            pool_recycle=db.DB_POOL_RECYCLE,
            # Critical: check connection liveness before use.
            # Prevents "server closed the connection unexpectedly" errors
            # when idle timeout kills the connection.
            pool_pre_ping=True,
            # SQL logging (disable in production for performance)
            echo=db.DB_ECHO,
            # asyncpg-specific: use unicode text for JSONB
            json_serializer=_json_serializer,
            json_deserializer=_json_deserializer,
        )
        logger.info(
            "PostgreSQL async engine created",
            extra={
                "pool_size": db.DB_POOL_SIZE,
                "max_overflow": db.DB_MAX_OVERFLOW,
            },
        )
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            # expire_on_commit=False is critical for async:
            # prevents "DetachedInstanceError" when accessing attributes
            # after commit (common pitfall in async SQLAlchemy).
            expire_on_commit=False,
            # autoflush=False: control when SQL is emitted.
            # Prevents surprising flushes mid-transaction.
            autoflush=False,
            autocommit=False,
        )
    return _session_factory


def _json_serializer(obj) -> str:
    """Custom JSON serializer for SQLAlchemy JSONB columns."""
    import json
    from decimal import Decimal
    from datetime import datetime, date

    class _Encoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, Decimal):
                return float(o)
            if isinstance(o, (datetime, date)):
                return o.isoformat()
            return super().default(o)

    return json.dumps(obj, cls=_Encoder)


def _json_deserializer(s: str):
    """Custom JSON deserializer for SQLAlchemy JSONB columns."""
    import json
    return json.loads(s)


# ── Session Context Manager ───────────────────────────────────────────────────

@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that provides a database session.

    Usage:
        async with get_db() as db:
            result = await db.execute(select(Product).where(...))
            products = result.scalars().all()

    Transaction handling:
        - Commits automatically on successful exit.
        - Rolls back on any exception.
        - Closes session after exit (returns connection to pool).

    In FastAPI:
        Use as dependency:
        async def route(db: AsyncSession = Depends(get_db_dep))
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_dep() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency version of get_db().

    Usage in FastAPI routes:
        from fastapi import Depends
        from core.database import get_db_dep

        @router.get("/products")
        async def list_products(db: AsyncSession = Depends(get_db_dep)):
            ...
    """
    async with get_db() as session:
        yield session


# ── Health Check ──────────────────────────────────────────────────────────────

async def check_database_health() -> dict:
    """
    Verify database connectivity. Called at startup.

    Returns:
        {"status": "ok", "latency_ms": 12.3}
        {"status": "error", "error": "..."}
    """
    import time
    start = time.monotonic()
    try:
        async with get_db() as db:
            await db.execute(text("SELECT 1"))
        latency_ms = (time.monotonic() - start) * 1000
        logger.info(f"Database health check OK ({latency_ms:.1f}ms)")
        return {"status": "ok", "latency_ms": round(latency_ms, 2)}
    except Exception as e:
        logger.error(f"Database health check FAILED: {e}")
        return {"status": "error", "error": str(e)}


# ── Schema Management ─────────────────────────────────────────────────────────

async def create_all_tables() -> None:
    """
    Create all tables that don't exist yet.

    Use ONLY in development or testing.
    In production, use: alembic upgrade head

    This function is NOT used by the production startup.
    Alembic handles schema migrations.
    """
    from core.models import Base
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("All tables created (development mode).")


async def drop_all_tables() -> None:
    """
    Drop ALL tables. DESTRUCTIVE.

    Use ONLY in testing. Never in production.
    Protected by an explicit safety check.
    """
    settings = get_settings()
    if settings.is_production:
        raise RuntimeError(
            "drop_all_tables() called in production environment. "
            "This is forbidden. Use alembic downgrade for controlled rollback."
        )
    from core.models import Base
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.warning("All tables DROPPED (test/dev mode).")


# ── Engine Lifecycle ──────────────────────────────────────────────────────────

async def close_engine() -> None:
    """
    Gracefully close all connections in the pool.

    Call on application shutdown to prevent connection leaks.

    In FastAPI: use lifespan context manager.
    In Celery: call in worker shutdown signal handler.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("PostgreSQL engine closed — all connections released.")


# ── Repository Utilities ──────────────────────────────────────────────────────
# These are standalone async functions, not model methods.
# Keeps models as pure data classes, business logic here.

async def get_by_id(db: AsyncSession, model_class, record_id) -> Optional[object]:
    """
    Fetch a model instance by primary key.

    Usage:
        async with get_db() as db:
            product = await get_by_id(db, Product, product_id)
    """
    from core.exceptions import RecordNotFoundError
    instance = await db.get(model_class, record_id)
    if instance is None:
        raise RecordNotFoundError(
            model=model_class.__name__,
            identifier=record_id,
        )
    return instance


async def save(db: AsyncSession, instance) -> object:
    """
    Add or merge an instance and flush to DB (without committing).

    Flush ensures the instance gets its auto-generated ID
    without committing the transaction.

    Usage:
        async with get_db() as db:
            product = Product(asin="B08XYZ123", ...)
            product = await save(db, product)
            # product.id is now set
    """
    db.add(instance)
    await db.flush()
    await db.refresh(instance)
    return instance
