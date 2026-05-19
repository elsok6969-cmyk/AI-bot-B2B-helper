from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import dateparser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import Client, Reminder, ReminderKind, ReminderStatus, User


def parse_when(when_str: str) -> datetime | None:
    """Parse a Russian natural-language datetime into UTC.

    Returns None if parsing fails. Biases toward future dates so that
    'пятница' or '10:00' map to the next occurrence.
    """
    when_str = when_str.strip()
    if not when_str:
        return None
    parsed = dateparser.parse(
        when_str,
        languages=["ru"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": settings.tz,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return None
    return parsed.astimezone(UTC)


def parse_remind_args(args: str) -> tuple[str, datetime, str] | str:
    """Parse `/remind <slug> <when> <text>` from a raw arg string.

    Returns either (slug, due_at_utc, text) on success, or a human-readable
    error message string on failure. The split between <when> and <text>
    is greedy from the right: we try progressively shorter <when> prefixes
    until one parses as a datetime, then everything after is <text>.
    """
    tokens = args.split()
    if len(tokens) < 3:
        return "Использование: <code>/remind &lt;slug&gt; &lt;когда&gt; &lt;текст&gt;</code>"

    slug = tokens[0]
    # Try the longest possible <when> first (up to 4 tokens), shrinking. The
    # first prefix that parses wins; if it consumes all remaining tokens we
    # surface "missing text" rather than retrying with a shorter prefix.
    for split in range(min(len(tokens) - 1, 4), 0, -1):
        when_str = " ".join(tokens[1 : 1 + split])
        due_at = parse_when(when_str)
        if due_at is None:
            continue
        text = " ".join(tokens[1 + split :]).strip()
        if not text:
            return "Не хватает текста напоминания после времени."
        if due_at <= datetime.now(UTC):
            return "Указанное время уже прошло. Назначь будущее."
        return slug, due_at, text

    return (
        "Не понял время. Примеры: <code>завтра 10:00</code>, "
        "<code>через 3 дня</code>, <code>пятница 14:00</code>."
    )


async def create_reminder(
    session: AsyncSession,
    *,
    client_id: UUID,
    user_id: UUID,
    due_at: datetime,
    text: str,
    kind: ReminderKind = ReminderKind.CUSTOM,
) -> Reminder:
    reminder = Reminder(
        client_id=client_id,
        user_id=user_id,
        kind=kind,
        due_at=due_at,
        payload={"text": text},
        status=ReminderStatus.PENDING,
    )
    session.add(reminder)
    await session.flush()
    return reminder


async def list_active_reminders(
    session: AsyncSession, *, user_id: UUID
) -> list[tuple[Reminder, Client]]:
    rows = await session.execute(
        select(Reminder, Client)
        .join(Client, Client.id == Reminder.client_id)
        .where(
            Reminder.user_id == user_id,
            Reminder.status.in_([ReminderStatus.PENDING, ReminderStatus.SENT]),
        )
        .order_by(Reminder.due_at)
    )
    return [(r, c) for r, c in rows.all()]


async def find_reminder_by_short_id(
    session: AsyncSession, *, user_id: UUID, short_id: str
) -> Reminder | None | str:
    """Resolve a reminder by an 8+ char UUID prefix scoped to the given user.

    Returns the Reminder, None if not found, or the string "ambiguous"
    if more than one matches.
    """
    prefix = short_id.strip().lower()
    if len(prefix) < 4:
        return None
    rows = (
        await session.execute(
            select(Reminder)
            .where(Reminder.user_id == user_id)
            .order_by(Reminder.created_at.desc())
        )
    ).scalars().all()
    matches = [r for r in rows if str(r.id).lower().startswith(prefix)]
    if not matches:
        return None
    if len(matches) > 1:
        return "ambiguous"
    return matches[0]


async def mark_done(session: AsyncSession, reminder: Reminder) -> None:
    reminder.status = ReminderStatus.DONE


async def find_due_reminders(session: AsyncSession) -> list[tuple[Reminder, Client, User]]:
    now = datetime.now(UTC)
    rows = await session.execute(
        select(Reminder, Client, User)
        .join(Client, Client.id == Reminder.client_id)
        .join(User, User.id == Reminder.user_id)
        .where(
            Reminder.status == ReminderStatus.PENDING,
            Reminder.due_at <= now,
        )
        .order_by(Reminder.due_at)
    )
    return [(r, c, u) for r, c, u in rows.all()]


def reminder_text(reminder: Reminder) -> str:
    payload: Any = reminder.payload or {}
    if isinstance(payload, dict):
        return str(payload.get("text") or payload.get("note") or "").strip()
    return ""


def short_id(value: UUID) -> str:
    return str(value).split("-", 1)[0]
