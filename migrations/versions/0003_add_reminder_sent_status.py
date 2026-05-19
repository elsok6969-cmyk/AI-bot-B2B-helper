"""add reminder sent status

Revision ID: 8b1a5e2c4f10
Revises: f3bed7dee6d5
Create Date: 2026-05-19 09:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "8b1a5e2c4f10"
down_revision: str | None = "f3bed7dee6d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL 12+ supports ALTER TYPE ADD VALUE inside a transaction.
    op.execute("ALTER TYPE reminder_status ADD VALUE IF NOT EXISTS 'SENT' AFTER 'PENDING'")


def downgrade() -> None:
    # PostgreSQL has no direct way to drop a single enum value; would require
    # rebuilding the type. Leaving downgrade as a no-op — manual cleanup needed
    # if a true rollback is required.
    pass
