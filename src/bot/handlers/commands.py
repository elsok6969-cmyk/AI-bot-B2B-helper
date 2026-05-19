from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message as TgMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.db.models import (
    BusinessConnection,
    Client,
    Conversation,
    MessageDirection,
    Reminder,
    ReminderStatus,
    User,
)
from src.db.models import Message as MessageModel

router = Router(name="commands")

DIRECTION_ARROW = {MessageDirection.IN: "←", MessageDirection.OUT: "→"}


def _local_tz() -> ZoneInfo:
    return ZoneInfo(settings.tz)


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone(_local_tz()).strftime("%Y-%m-%d %H:%M")


def _truncate(text: str | None, limit: int = 80) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@router.message(Command("start"))
async def cmd_start(message: TgMessage, manager: User, session: AsyncSession) -> None:
    name_safe = html.escape(manager.name or "менеджер")

    connected = await session.scalar(
        select(BusinessConnection).where(
            BusinessConnection.user_id == manager.id,
            BusinessConnection.is_enabled.is_(True),
        )
    )

    if connected is None:
        await message.answer(
            f"Привет, {name_safe}!\n\n"
            "Чтобы я начал видеть переписку с твоими клиентами, подключи меня в "
            "Telegram:\n"
            "<b>Настройки → Telegram Business → Chatbots → выбери этого бота</b> "
            "и включи право <i>Reply</i>."
        )
        return

    await message.answer(
        f"Привет, {name_safe}! Подключение к Telegram Business активно.\n\n"
        "Команды:\n"
        "/clients — список клиентов\n"
        "/client &lt;slug&gt; — карточка клиента"
    )


@router.message(Command("clients"))
async def cmd_clients(
    message: TgMessage,
    manager: User,
    session: AsyncSession,
) -> None:
    stmt = (
        select(Client, func.count(MessageModel.id).label("msg_count"))
        .select_from(Client)
        .outerjoin(Conversation, Conversation.client_id == Client.id)
        .outerjoin(MessageModel, MessageModel.conversation_id == Conversation.id)
        .where(Client.org_id == manager.org_id)
        .group_by(Client.id)
        .order_by(Client.last_touch_at.is_(None), Client.last_touch_at.desc())
    )
    rows = (await session.execute(stmt)).all()

    if not rows:
        await message.answer("Клиентов пока нет.")
        return

    lines = ["<b>Клиенты:</b>"]
    for client, msg_count in rows:
        name = html.escape(client.name or client.slug)
        slug = html.escape(client.slug)
        lines.append(
            f"• <code>{slug}</code> — {name} | сообщений: {msg_count} | "
            f"last: {_fmt_dt(client.last_touch_at)}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("client"))
async def cmd_client(
    message: TgMessage,
    manager: User,
    session: AsyncSession,
    command: CommandObject,
) -> None:
    slug = (command.args or "").strip()
    if not slug:
        await message.answer("Использование: <code>/client &lt;slug&gt;</code>")
        return

    client = await session.scalar(
        select(Client)
        .where(Client.org_id == manager.org_id, Client.slug == slug)
        .options(selectinload(Client.profile))
    )
    if client is None:
        await message.answer(f"Клиент <code>{html.escape(slug)}</code> не найден.")
        return

    recent_messages = (
        await session.execute(
            select(MessageModel)
            .join(Conversation, Conversation.id == MessageModel.conversation_id)
            .where(Conversation.client_id == client.id)
            .order_by(MessageModel.sent_at.desc().nulls_last(), MessageModel.created_at.desc())
            .limit(10)
        )
    ).scalars().all()

    reminders = (
        await session.execute(
            select(Reminder)
            .where(
                Reminder.client_id == client.id,
                Reminder.status == ReminderStatus.PENDING,
            )
            .order_by(Reminder.due_at)
        )
    ).scalars().all()

    lines: list[str] = []
    title_name = html.escape(client.name or "—")
    lines.append(f"<b>📋 Клиент:</b> <code>{html.escape(client.slug)}</code> — {title_name}")
    if client.telegram_username:
        lines.append(f"@{html.escape(client.telegram_username)}")
    lines.append(
        f"Стадия: <b>{client.stage.value}</b> | Тип: <b>{client.business_type.value}</b> | "
        f"Температура: <b>{client.temperature.value}</b>"
    )
    lines.append(f"Последний контакт: {_fmt_dt(client.last_touch_at)}")

    profile = client.profile
    if profile is not None:
        lines.append("")
        lines.append("<b>📝 Профиль</b>")
        if profile.summary:
            lines.append(f"<i>Резюме:</i> {html.escape(profile.summary)}")
        if profile.pain_points:
            joined = "; ".join(html.escape(p) for p in profile.pain_points)
            lines.append(f"<i>Боли:</i> {joined}")
        if profile.objections:
            joined = "; ".join(html.escape(p) for p in profile.objections)
            lines.append(f"<i>Возражения:</i> {joined}")
        if profile.won_arguments:
            joined = "; ".join(html.escape(p) for p in profile.won_arguments)
            lines.append(f"<i>Сильные аргументы:</i> {joined}")
        if profile.personal_notes:
            lines.append(f"<i>Личные заметки:</i> {html.escape(profile.personal_notes)}")

    lines.append("")
    lines.append("<b>💬 Последние сообщения</b>")
    if not recent_messages:
        lines.append("<i>Сообщений пока нет.</i>")
    else:
        for m in reversed(recent_messages):
            arrow = DIRECTION_ARROW[m.direction]
            ts = _fmt_dt(m.sent_at or m.created_at)
            body = html.escape(_truncate(m.text))
            lines.append(f"{arrow} {ts} {body}")

    lines.append("")
    lines.append("<b>⏰ Активные напоминания</b>")
    if not reminders:
        lines.append("<i>Нет активных напоминаний.</i>")
    else:
        for r in reminders:
            note = ""
            if r.payload and isinstance(r.payload, dict):
                what = r.payload.get("text") or r.payload.get("note")
                if what:
                    note = f" — {html.escape(str(what))}"
            lines.append(f"• {_fmt_dt(r.due_at)} <b>{r.kind.value}</b>{note}")

    await message.answer("\n".join(lines))
