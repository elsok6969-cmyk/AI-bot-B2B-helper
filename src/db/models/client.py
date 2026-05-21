from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.client_profile import ClientProfile
    from src.db.models.conversation import Conversation
    from src.db.models.organization import Organization
    from src.db.models.reminder import Reminder
    from src.db.models.user import User


class BusinessType(str, enum.Enum):
    RETAIL = "retail"
    ONLINE = "online"
    HORECA = "horeca"
    CORP = "corp"
    UNKNOWN = "unknown"


class ClientStage(str, enum.Enum):
    LEAD = "lead"
    QUALIFIED = "qualified"
    NEGOTIATION = "negotiation"
    CONTRACT = "contract"
    ACTIVE = "active"
    LOST = "lost"


class ClientTemperature(str, enum.Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_clients_org_slug"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), nullable=False)

    business_type: Mapped[BusinessType] = mapped_column(
        Enum(BusinessType, name="business_type", native_enum=True),
        nullable=False,
        default=BusinessType.UNKNOWN,
    )
    est_volume: Mapped[int | None] = mapped_column(Integer)
    interest_categories: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    stage: Mapped[ClientStage] = mapped_column(
        Enum(ClientStage, name="client_stage", native_enum=True),
        nullable=False,
        default=ClientStage.LEAD,
    )
    temperature: Mapped[ClientTemperature] = mapped_column(
        Enum(ClientTemperature, name="client_temperature", native_enum=True),
        nullable=False,
        default=ClientTemperature.COLD,
    )

    last_touch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization: Mapped[Organization] = relationship(back_populates="clients")
    owner: Mapped[User | None] = relationship(back_populates="owned_clients")
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    profile: Mapped[ClientProfile | None] = relationship(
        back_populates="client", cascade="all, delete-orphan", uselist=False
    )
    reminders: Mapped[list[Reminder]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
