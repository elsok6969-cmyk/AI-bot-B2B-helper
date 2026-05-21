"""add web/telethon/mail tables, drafts, client email, new message sources

Revision ID: a4f9c1b8d2e3
Revises: 8b1a5e2c4f10
Create Date: 2026-05-21 13:30:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a4f9c1b8d2e3"
down_revision: str | None = "8b1a5e2c4f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- extend enums ----
    op.execute("ALTER TYPE message_source ADD VALUE IF NOT EXISTS 'TELETHON_USER'")
    op.execute("ALTER TYPE message_source ADD VALUE IF NOT EXISTS 'MANUAL_TEXT'")

    # ---- clients.email ----
    op.add_column("clients", sa.Column("email", sa.String(length=255), nullable=True))
    op.create_index("ix_clients_email", "clients", ["email"])

    # ---- telethon_accounts ----
    op.create_table(
        "telethon_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("session_encrypted", sa.Text(), nullable=True),
        sa.Column("is_authorized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("login_phase", sa.String(length=32), nullable=True),
        sa.Column("phone_code_hash", sa.String(length=255), nullable=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telethon_accounts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telethon_accounts")),
        sa.UniqueConstraint("user_id", name=op.f("uq_telethon_accounts_user_id")),
    )

    # ---- mailboxes ----
    op.create_table(
        "mailboxes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "imap_host",
            sa.String(length=255),
            nullable=False,
            server_default="imap.yandex.ru",
        ),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column(
            "smtp_host",
            sa.String(length=255),
            nullable=False,
            server_default="smtp.yandex.ru",
        ),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="465"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_uid_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mailboxes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mailboxes")),
        sa.UniqueConstraint("user_id", name=op.f("uq_mailboxes_user_id")),
    )

    # ---- drafts ----
    op.create_table(
        "drafts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("source_message_id", sa.UUID(), nullable=True),
        sa.Column(
            "channel",
            sa.Enum("TG_BUSINESS", "TELETHON_USER", "EMAIL", name="draft_channel"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SENT", "REJECTED", name="draft_status"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("variants", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("selected_text", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_drafts_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_drafts_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_drafts_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.id"],
            name=op.f("fk_drafts_source_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drafts")),
    )
    op.create_index("ix_drafts_status", "drafts", ["status"])
    op.create_index("ix_drafts_client_id", "drafts", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_drafts_client_id", table_name="drafts")
    op.drop_index("ix_drafts_status", table_name="drafts")
    op.drop_table("drafts")
    op.execute("DROP TYPE IF EXISTS draft_channel")
    op.execute("DROP TYPE IF EXISTS draft_status")
    op.drop_table("mailboxes")
    op.drop_table("telethon_accounts")
    op.drop_index("ix_clients_email", table_name="clients")
    op.drop_column("clients", "email")
    # Cannot remove enum values cleanly in PostgreSQL — leaving in place.
