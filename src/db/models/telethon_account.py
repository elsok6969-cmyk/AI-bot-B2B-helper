from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class TelethonAccount(Base):
    """A user's personal Telegram account, accessed via Telethon.

    ``session_encrypted`` is the Telethon StringSession encrypted with the
    Fernet key from ``settings.secrets_key``. Login is a two-step flow
    (send_code → sign_in) handled by the web UI; ``login_phase`` and
    ``phone_code_hash`` track the in-progress flow.
    """

    __tablename__ = "telethon_accounts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    phone: Mapped[str | None] = mapped_column(String(32))
    session_encrypted: Mapped[str | None] = mapped_column(Text)
    is_authorized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    login_phase: Mapped[str | None] = mapped_column(String(32))  # idle|code_sent|password_needed
    phone_code_hash: Mapped[str | None] = mapped_column(String(255))

    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
