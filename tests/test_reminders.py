"""Tests for reminder parsing, creation, and time-based firing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun import freeze_time

from src.db.models import ReminderKind, ReminderStatus
from src.services.clients import create_manual_client
from src.services.reminders import (
    create_reminder,
    find_due_reminders,
    find_reminder_by_short_id,
    list_active_reminders,
    mark_done,
    parse_remind_args,
    parse_when,
    reminder_text,
    short_id,
)


def test_parse_when_handles_russian_phrasings():
    with freeze_time("2026-05-19 12:00:00+03:00"):
        assert parse_when("завтра 10:00") is not None
        assert parse_when("через 3 дня") is not None
        assert parse_when("пятница 14:00") is not None
        assert parse_when("полный бред") is None


def test_parse_remind_args_requires_text():
    with freeze_time("2026-05-19 12:00:00+03:00"):
        # No text after the time expression
        result = parse_remind_args("vasya завтра 10:00")
        assert isinstance(result, str) and "текста" in result.lower()


def test_parse_remind_args_rejects_past_time():
    with freeze_time("2026-05-19 12:00:00+03:00"):
        # Trying a past datetime expression — dateparser actually picks the
        # next future occurrence for relative words, so we test the explicit
        # past-time error via an obviously past wall-clock time.
        result = parse_remind_args("vasya 2020-01-01 догнать")
        assert isinstance(result, str) and "прошло" in result.lower()


def test_parse_remind_args_returns_tuple_on_success():
    with freeze_time("2026-05-19 12:00:00+03:00"):
        ok = parse_remind_args("vasya завтра 10:00 уточнить объёмы и категории")
        assert not isinstance(ok, str)
        slug, due_at, text = ok
        assert slug == "vasya"
        assert "уточнить объёмы" in text
        assert due_at.tzinfo is not None
        # Should be tomorrow at 10:00 Moscow → 07:00 UTC
        assert due_at.hour == 7


@pytest.mark.asyncio
async def test_create_and_close_reminder(session, manager):
    client = await create_manual_client(
        session, manager.org_id, name="Клиент", owner_user_id=manager.id
    )
    due = datetime.now(UTC) + timedelta(hours=1)
    reminder = await create_reminder(
        session,
        client_id=client.id,
        user_id=manager.id,
        due_at=due,
        text="Перезвонить и подтвердить",
    )
    await session.commit()

    assert reminder.status == ReminderStatus.PENDING
    assert reminder.kind == ReminderKind.CUSTOM
    assert reminder_text(reminder) == "Перезвонить и подтвердить"

    active = await list_active_reminders(session, user_id=manager.id)
    assert len(active) == 1

    found = await find_reminder_by_short_id(
        session, user_id=manager.id, short_id=short_id(reminder.id)
    )
    assert found is not None and not isinstance(found, str)
    assert found.id == reminder.id

    await mark_done(session, reminder)
    await session.commit()

    active = await list_active_reminders(session, user_id=manager.id)
    assert active == []


@pytest.mark.asyncio
async def test_find_due_reminders_only_returns_past_pending(session, manager):
    client = await create_manual_client(
        session, manager.org_id, name="Клиент", owner_user_id=manager.id
    )

    fixed_now = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    past = await create_reminder(
        session,
        client_id=client.id,
        user_id=manager.id,
        due_at=fixed_now - timedelta(minutes=5),
        text="Прошлое",
    )
    future = await create_reminder(
        session,
        client_id=client.id,
        user_id=manager.id,
        due_at=fixed_now + timedelta(hours=1),
        text="Будущее",
    )
    already_sent = await create_reminder(
        session,
        client_id=client.id,
        user_id=manager.id,
        due_at=fixed_now - timedelta(hours=1),
        text="Уже отправлено",
    )
    already_sent.status = ReminderStatus.SENT
    await session.commit()

    with freeze_time(fixed_now):
        due = await find_due_reminders(session)

    due_ids = {r.id for r, _, _ in due}
    assert past.id in due_ids
    assert future.id not in due_ids
    assert already_sent.id not in due_ids
