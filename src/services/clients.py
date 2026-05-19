from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    BusinessType,
    Client,
    Conversation,
    ConversationPlatform,
)
from src.utils.slug import slugify


def _display_name(
    *,
    first_name: str | None,
    last_name: str | None,
    username: str | None,
    telegram_user_id: int,
) -> str:
    full = " ".join(part for part in (first_name, last_name) if part)
    return full or username or f"user_{telegram_user_id}"


async def _unique_slug(session: AsyncSession, org_id: UUID, base: str) -> str:
    candidate = base
    suffix = 2
    while True:
        existing = await session.scalar(
            select(Client.id).where(Client.org_id == org_id, Client.slug == candidate)
        )
        if existing is None:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


async def resolve_or_create_client(
    session: AsyncSession,
    org_id: UUID,
    *,
    telegram_user_id: int,
    first_name: str | None,
    last_name: str | None,
    username: str | None,
) -> Client:
    client = await session.scalar(
        select(Client).where(
            Client.org_id == org_id,
            Client.telegram_user_id == telegram_user_id,
        )
    )
    if client is not None:
        if username and client.telegram_username != username:
            client.telegram_username = username
        new_name = _display_name(
            first_name=first_name,
            last_name=last_name,
            username=username,
            telegram_user_id=telegram_user_id,
        )
        if not client.name and new_name:
            client.name = new_name
        return client

    name = _display_name(
        first_name=first_name,
        last_name=last_name,
        username=username,
        telegram_user_id=telegram_user_id,
    )
    base_slug = slugify(username or name)
    slug = await _unique_slug(session, org_id, base_slug)

    client = Client(
        org_id=org_id,
        telegram_user_id=telegram_user_id,
        telegram_username=username,
        name=name,
        slug=slug,
    )
    session.add(client)
    await session.flush()
    return client


async def resolve_or_create_conversation(
    session: AsyncSession,
    *,
    client_id: UUID,
    user_id: UUID,
    platform: ConversationPlatform = ConversationPlatform.TELEGRAM,
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.client_id == client_id,
            Conversation.user_id == user_id,
            Conversation.platform == platform,
        )
    )
    if conversation is not None:
        return conversation

    conversation = Conversation(
        client_id=client_id,
        user_id=user_id,
        platform=platform,
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def create_manual_client(
    session: AsyncSession,
    org_id: UUID,
    *,
    name: str,
    owner_user_id: UUID,
    business_type: BusinessType = BusinessType.UNKNOWN,
    est_volume: int | None = None,
    interest_categories: list[str] | None = None,
) -> Client:
    """Create a Client without a Telegram identity (manual / non-TG channel)."""
    name = name.strip()
    base_slug = slugify(name)
    slug = await _unique_slug(session, org_id, base_slug)

    client = Client(
        org_id=org_id,
        telegram_user_id=None,
        telegram_username=None,
        name=name,
        slug=slug,
        business_type=business_type,
        est_volume=est_volume,
        interest_categories=interest_categories or [],
        owner_user_id=owner_user_id,
    )
    session.add(client)
    await session.flush()
    return client
