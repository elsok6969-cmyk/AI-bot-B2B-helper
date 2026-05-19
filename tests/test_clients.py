"""Tests for client resolution and manual creation."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.db.models import BusinessType, Client
from src.services.clients import (
    create_manual_client,
    resolve_or_create_client,
    resolve_or_create_conversation,
)


@pytest.mark.asyncio
async def test_resolve_or_create_client_dedups_by_tg_id(session, manager):
    first = await resolve_or_create_client(
        session,
        manager.org_id,
        telegram_user_id=999,
        first_name="Вася",
        last_name="Пупкин",
        username="vasya",
    )
    await session.commit()

    second = await resolve_or_create_client(
        session,
        manager.org_id,
        telegram_user_id=999,
        first_name="Вася",
        last_name="Изменивший Фамилию",
        username="vasya",
    )
    await session.commit()

    assert second.id == first.id
    count = await session.scalar(
        select(func.count(Client.id)).where(Client.org_id == manager.org_id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_resolve_or_create_client_updates_username(session, manager):
    client = await resolve_or_create_client(
        session,
        manager.org_id,
        telegram_user_id=999,
        first_name="Вася",
        last_name=None,
        username=None,
    )
    await session.commit()
    assert client.telegram_username is None

    # Same TG id, now has a username — should propagate
    again = await resolve_or_create_client(
        session,
        manager.org_id,
        telegram_user_id=999,
        first_name="Вася",
        last_name=None,
        username="vasya_new",
    )
    await session.commit()
    assert again.id == client.id
    assert again.telegram_username == "vasya_new"


@pytest.mark.asyncio
async def test_slug_collision_appends_suffix(session, manager):
    """Both auto-resolved and manual-creation paths must respect (org_id, slug)."""
    a = await resolve_or_create_client(
        session,
        manager.org_id,
        telegram_user_id=1001,
        first_name="Вася",
        last_name="Пупкин",
        username="vasya",
    )
    b = await resolve_or_create_client(
        session,
        manager.org_id,
        telegram_user_id=1002,
        first_name="Вася",
        last_name="Не Пупкин",
        username="vasya",
    )
    await session.commit()

    assert a.slug == "vasya"
    assert b.slug == "vasya-2"

    manual = await create_manual_client(
        session,
        manager.org_id,
        name="vasya",
        owner_user_id=manager.id,
    )
    await session.commit()
    # The previous two clients already occupy "vasya" and "vasya-2"
    assert manual.slug == "vasya-3"


@pytest.mark.asyncio
async def test_create_manual_client_has_no_tg_id(session, manager):
    client = await create_manual_client(
        session,
        manager.org_id,
        name="ИП Сидоров",
        owner_user_id=manager.id,
        business_type=BusinessType.RETAIL,
        est_volume=500_000,
        interest_categories=["парфюм"],
    )
    await session.commit()

    assert client.telegram_user_id is None
    assert client.telegram_username is None
    assert client.business_type == BusinessType.RETAIL
    assert client.est_volume == 500_000
    assert client.interest_categories == ["парфюм"]
    assert client.owner_user_id == manager.id
    assert client.slug == "ip-sidorov"


@pytest.mark.asyncio
async def test_resolve_or_create_conversation_dedups(session, manager):
    client = await resolve_or_create_client(
        session,
        manager.org_id,
        telegram_user_id=999,
        first_name="Вася",
        last_name=None,
        username="vasya",
    )
    a = await resolve_or_create_conversation(session, client_id=client.id, user_id=manager.id)
    b = await resolve_or_create_conversation(session, client_id=client.id, user_id=manager.id)
    await session.commit()
    assert a.id == b.id
