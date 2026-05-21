from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class Mailbox(Base):
    """A Yandex (or other IMAP/SMTP) mailbox owned by a manager.

    ``password_encrypted`` is the Yandex app password (NOT the main
    account password) encrypted with the Fernet key from
    ``settings.secrets_key``. ``last_uid_seen`` is the highest UID the
    IMAP poller has already ingested for this mailbox.
    """

    __tablename__ = "mailboxes"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    imap_host: Mapped[str] = mapped_column(String(255), nullable=False, default="imap.yandex.ru")
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False, default="smtp.yandex.ru")
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=465)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_uid_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship()
