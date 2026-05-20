from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.business_connection import BusinessConnection
    from src.db.models.client import Client
    from src.db.models.conversation import Conversation
    from src.db.models.organization import Organization
    from src.db.models.reminder import Reminder


class UserRole(str, enum.Enum):
    OWNER = "owner"
    MANAGER = "manager"


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=True),
        nullable=False,
        default=UserRole.MANAGER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="users")
    business_connections: Mapped[list[BusinessConnection]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    owned_clients: Mapped[list[Client]] = relationship(back_populates="owner")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="user")
    reminders: Mapped[list[Reminder]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
