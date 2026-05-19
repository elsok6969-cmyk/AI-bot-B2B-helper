"""Tests for digest collection and bucket categorization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models import (
    BusinessType,
    ClientStage,
    ConversationPlatform,
    MessageDirection,
    MessageSource,
    Reminder,
    ReminderKind,
    ReminderStatus,
)
from src.db.models import Message as MessageModel
from src.services.clients import create_manual_client, resolve_or_create_conversation
from src.services.digest_service import collect_digest, format_digest_html


@pytest.mark.asyncio
async def test_digest_categorizes_clients_into_buckets(session, manager):
    now = datetime.now(UTC)

    c_reply = await create_manual_client(
        session, manager.org_id, name="Ждёт Ответа", owner_user_id=manager.id
    )
    c_cold = await create_manual_client(
        session,
        manager.org_id,
        name="Остывший",
        owner_user_id=manager.id,
        business_type=BusinessType.ONLINE,
    )
    c_hot = await create_manual_client(
        session, manager.org_id, name="Горячий", owner_user_id=manager.id
    )
    c_quiet = await create_manual_client(
        session, manager.org_id, name="Тихий", owner_user_id=manager.id
    )

    # needs_reply: inbound newer than last outbound
    c_reply.last_inbound_at = now - timedelta(hours=1)
    c_reply.last_outbound_at = now - timedelta(hours=4)
    c_reply.last_touch_at = c_reply.last_inbound_at

    # cold: NEGOTIATION, last_touch_at > 7 days ago
    c_cold.stage = ClientStage.NEGOTIATION
    c_cold.last_touch_at = now - timedelta(days=10)
    c_cold.last_inbound_at = c_cold.last_touch_at
    c_cold.last_outbound_at = c_cold.last_touch_at - timedelta(days=1)

    # hot: high-urgency inbound in last 24h, no outbound after
    conv_hot = await resolve_or_create_conversation(
        session,
        client_id=c_hot.id,
        user_id=manager.id,
        platform=ConversationPlatform.MANUAL,
    )
    hot_msg = MessageModel(
        conversation_id=conv_hot.id,
        direction=MessageDirection.IN,
        source=MessageSource.MANUAL_SCREENSHOT,
        text="Где отгрузка?!",
        raw_payload={},
        analysis={
            "intent": "complaint",
            "sentiment": "negative",
            "urgency": "high",
            "suggested_action": "Извиниться, ускорить отгрузку.",
        },
        sent_at=now - timedelta(hours=2),
    )
    session.add(hot_msg)
    c_hot.last_inbound_at = hot_msg.sent_at
    c_hot.last_touch_at = hot_msg.sent_at

    # quiet: outbound today, nothing else — should be in no bucket
    c_quiet.last_outbound_at = now - timedelta(hours=1)
    c_quiet.last_touch_at = now - timedelta(hours=1)

    # A reminder due today
    due_today = (now.replace(hour=23, minute=0, second=0, microsecond=0)) - timedelta(hours=8)
    if due_today < now:
        due_today = now + timedelta(hours=1)
    reminder = Reminder(
        client_id=c_reply.id,
        user_id=manager.id,
        kind=ReminderKind.FOLLOWUP,
        due_at=due_today,
        payload={"text": "Перезвонить"},
        status=ReminderStatus.PENDING,
    )
    session.add(reminder)

    await session.commit()

    digest = await collect_digest(session, manager, now=now)

    needs_slugs = {c.slug for c in digest.needs_reply}
    cold_slugs = {c.slug for c in digest.cold}
    hot_slugs = {h.client.slug for h in digest.hot}

    assert c_reply.slug in needs_slugs
    assert c_cold.slug in cold_slugs
    assert c_hot.slug in hot_slugs
    assert c_quiet.slug not in needs_slugs
    assert c_quiet.slug not in cold_slugs
    assert c_quiet.slug not in hot_slugs
    assert len(digest.today_reminders) == 1
    assert not digest.is_empty()

    html_text = format_digest_html(digest)
    assert "Горящие сигналы" in html_text
    assert "Ждут ответа" in html_text
    assert "Остывшие" in html_text
    assert "Напоминания на сегодня" in html_text


@pytest.mark.asyncio
async def test_digest_skips_hot_signal_with_subsequent_outbound(session, manager):
    """If the manager already replied after the high-urgency message, drop it."""
    now = datetime.now(UTC)

    c = await create_manual_client(
        session, manager.org_id, name="Реакция Дана", owner_user_id=manager.id
    )
    conv = await resolve_or_create_conversation(
        session,
        client_id=c.id,
        user_id=manager.id,
        platform=ConversationPlatform.MANUAL,
    )
    hot = MessageModel(
        conversation_id=conv.id,
        direction=MessageDirection.IN,
        source=MessageSource.MANUAL_SCREENSHOT,
        text="Где отгрузка?",
        raw_payload={},
        analysis={
            "urgency": "high",
            "intent": "complaint",
            "sentiment": "negative",
            "suggested_action": "",
        },
        sent_at=now - timedelta(hours=2),
    )
    session.add(hot)
    c.last_inbound_at = hot.sent_at
    c.last_outbound_at = now - timedelta(minutes=30)  # responded after
    c.last_touch_at = c.last_outbound_at
    await session.commit()

    digest = await collect_digest(session, manager, now=now)
    assert digest.hot == []


@pytest.mark.asyncio
async def test_digest_is_empty_for_pristine_manager(session, manager):
    digest = await collect_digest(session, manager)
    assert digest.is_empty()
    assert "Хорошего дня" in format_digest_html(digest)
