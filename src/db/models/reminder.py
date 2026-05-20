from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.client import Client
    from src.db.models.user import User


class ReminderKind(str, enum.Enum):
    FOLLOWUP = "followup"
    NUDGE = "nudge"
    CUSTOM = "custom"


class ReminderStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    DONE = "done"
    SNOOZED = "snoozed"


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[ReminderKind] = mapped_column(
        Enum(ReminderKind, name="reminder_kind", native_enum=True), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus, name="reminder_status", native_enum=True),
        nullable=False,
        default=ReminderStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped[Client] = relationship(back_populates="reminders")
    user: Mapped[User] = relationship(back_populates="reminders")
