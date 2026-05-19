"""Test fixtures for the Mynota bot.

Requires a Postgres instance accessible via TEST_DATABASE_URL (defaults
to ``postgresql+asyncpg://postgres@localhost:5433/mynota_test``). The
fixture session truncates all data-bearing tables before each test so
tests start from a clean slate; migrations are applied once for the
whole pytest session via the ``setup_database`` autouse fixture.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator

# Configure environment BEFORE importing any src.* module so settings pick up
# the test database.
os.environ.setdefault(
    "DATABASE_URL",
    os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres@localhost:5433/mynota_test",
    ),
)
os.environ.setdefault("BOT_TOKEN", "0:test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OWNER_TELEGRAM_ID", "1")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

_DATA_TABLES = (
    "ai_logs",
    "reminders",
    "messages",
    "client_profiles",
    "conversations",
    "clients",
    "business_connections",
    "users",
    "organizations",
)


@pytest.fixture(scope="session", autouse=True)
def setup_database() -> None:
    """Run alembic upgrade head once per pytest session."""
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True,
        env={**os.environ},
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )


@pytest_asyncio.fixture(autouse=True)
async def _truncate_tables() -> AsyncIterator[None]:
    """Clear data tables before each test so order doesn't matter."""
    from src.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE TABLE " + ", ".join(_DATA_TABLES) + " RESTART IDENTITY CASCADE")
        )
    yield


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    from src.db.session import SessionLocal

    async with SessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def manager(session: AsyncSession):
    """A seeded Organization + owner User."""
    from src.db.models import Organization, User, UserRole

    org = Organization(name="Mynota Test")
    session.add(org)
    await session.flush()
    user = User(
        org_id=org.id,
        telegram_user_id=42,
        name="Owner Test",
        role=UserRole.OWNER,
    )
    session.add(user)
    await session.commit()
    return user
