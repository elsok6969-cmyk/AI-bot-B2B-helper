from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.client import Client
    from src.db.models.conversation import Conversation
    from src.db.models.message import Message
    from src.db.models.user import User


class DraftChannel(str, enum.Enum):
    TG_BUSINESS = "tg_business"
    TELETHON_USER = "telethon_user"
    EMAIL = "email"


class DraftStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    REJECTED = "rejected"


class Draft(Base):
    """An AI-generated reply waiting for the manager's approval.

    Created automatically after each inbound message when
    ``settings.drafts_autogenerate`` is on. The user sees the queue in the
    web UI, edits if needed, and clicks Send — which routes the text
    through the right channel (TG bot, Telethon, SMTP).
    """

    __tablename__ = "drafts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )

    channel: Mapped[DraftChannel] = mapped_column(
        Enum(DraftChannel, name="draft_channel", native_enum=True), nullable=False
    )
    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus, name="draft_status", native_enum=True),
        nullable=False,
        default=DraftStatus.PENDING,
    )

    variants: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    selected_text: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client: Mapped[Client] = relationship()
    conversation: Mapped[Conversation] = relationship()
    user: Mapped[User | None] = relationship()
    source_message: Mapped[Message | None] = relationship()
