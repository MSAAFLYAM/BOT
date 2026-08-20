"""
alembic/env.py — Alembic migration environment.

Architecture decisions:
  - Uses asyncio.run() to execute async engine operations synchronously.
    Alembic itself is synchronous, but our engine is async (asyncpg).
    Solution: run_sync() inside a sync context.
  - DATABASE_URL loaded from environment.
    URL converted to sync (psycopg2) format for Alembic compatibility.
  - target_metadata set to Base.metadata (all models imported in __init__.py).
    Alembic compares this metadata against current DB schema to detect changes.
  - compare_type=True: detect column type changes (not just add/remove).
  -     render_as_batch=True: enables batch mode for SQLite compatibility
    (not strictly needed for PostgreSQL but good practice).
"""
import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import create_async_engine

# ── Path setup ────────────────────────────────────────────────────────────────
# Add project root to Python path so `from core.models import Base` works
# when running `alembic upgrade head` from the project root.
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ── Imports ───────────────────────────────────────────────────────────────────
# Import ALL models so Alembic discovers them via Base.metadata.
# If a model is not imported here, Alembic won't detect its changes.
from core.models import Base  # noqa: E402 — imports all models via __init__.py

# ── Configuration ─────────────────────────────────────────────────────────────
config = context.config

# Set up Python logging from alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata: ALL models registered in Base.metadata
target_metadata = Base.metadata


def get_url() -> str:
    """
    Get database URL from environment.

    Priority:
      1. DATABASE_URL environment variable.
      2. Alembic config file (alembic.ini — not set in our config).

    The URL is converted to synchronous format (psycopg2) because
    Alembic's migration runner is synchronous.
    asyncpg cannot be used directly with Alembic.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise ValueError(
            "DATABASE_URL environment variable is not set. "
            "Set DATABASE_URL environment variable, "
            "or set DATABASE_URL manually."
        )
    # Convert asyncpg URL → psycopg2 URL for Alembic sync context
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgres://", "postgresql://")
    return url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    Generates SQL scripts without connecting to the database.
    Useful for reviewing what migrations will do before applying.

    Usage: alembic upgrade head --sql
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Execute migrations using the provided synchronous connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # Include schema information for ENUM types
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online_async() -> None:
    """
    Run migrations in 'online' mode using async engine.

    We create a sync connection from the async engine using
    run_sync() — this is the recommended pattern for Alembic + asyncio.
    """
    url = get_url()
    # Create a synchronous engine wrapper (NOT asyncpg — use psycopg2)
    # asyncpg doesn't support synchronous migration execution.
    from sqlalchemy import create_engine
    sync_engine = create_engine(
        url,
        # No connection pool for migrations — single connection
        poolclass=pool.NullPool,
        # Echo SQL for migration transparency
        echo=True,
    )
    with sync_engine.connect() as connection:
        do_run_migrations(connection)
    sync_engine.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations."""
    asyncio.run(run_migrations_online_async())


# ── Main Entry Point ──────────────────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

