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
    ClientProfile,
    Conversation,
    MessageDirection,
    Reminder,
    ReminderStatus,
    User,
)
from src.db.models import Message as MessageModel
from src.services.ai_pipeline import suggest_reply_for_client
from src.services.reminders import (
    create_reminder,
    find_reminder_by_short_id,
    list_active_reminders,
    mark_done,
    parse_remind_args,
    reminder_text,
    short_id,
)

router = Router(name="commands")

DIRECTION_ARROW = {MessageDirection.IN: "←", MessageDirection.OUT: "→"}

VARIANT_LABEL_TITLES = {
    "formal": "Формальный",
    "friendly": "Дружелюбный",
    "closing": "Закрывающий",
}


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
        (
            await session.execute(
                select(MessageModel)
                .join(Conversation, Conversation.id == MessageModel.conversation_id)
                .where(Conversation.client_id == client.id)
                .order_by(MessageModel.sent_at.desc().nulls_last(), MessageModel.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )

    reminders = (
        (
            await session.execute(
                select(Reminder)
                .where(
                    Reminder.client_id == client.id,
                    Reminder.status == ReminderStatus.PENDING,
                )
                .order_by(Reminder.due_at)
            )
        )
        .scalars()
        .all()
    )

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


@router.message(Command("suggest"))
async def cmd_suggest(
    message: TgMessage,
    manager: User,
    session: AsyncSession,
    command: CommandObject,
) -> None:
    slug = (command.args or "").strip()
    if not slug:
        await message.answer("Использование: <code>/suggest &lt;slug&gt;</code>")
        return

    pending = await message.answer("⏳ Генерирую варианты ответа...")

    try:
        client, variants = await suggest_reply_for_client(session, manager=manager, slug=slug)
    except Exception:
        await pending.edit_text("❌ Не удалось получить варианты ответа.")
        raise

    if client is None:
        await pending.edit_text(f"Клиент <code>{html.escape(slug)}</code> не найден.")
        return
    if not variants:
        await pending.edit_text(
            "Не получилось сгенерировать варианты. Возможно, переписки ещё нет."
        )
        return

    header = f"💡 <b>Варианты ответа для</b> <code>{html.escape(client.slug)}</code>"
    blocks: list[str] = [header]
    for variant in variants:
        title = VARIANT_LABEL_TITLES.get(variant["label"], variant["label"].title())
        blocks.append(
            f"\n📌 <b>{html.escape(title)}</b>\n<code>{html.escape(variant['text'])}</code>"
        )
    await pending.edit_text("\n".join(blocks))


@router.message(Command("profile"))
async def cmd_profile(
    message: TgMessage,
    manager: User,
    session: AsyncSession,
    command: CommandObject,
) -> None:
    slug = (command.args or "").strip()
    if not slug:
        await message.answer("Использование: <code>/profile &lt;slug&gt;</code>")
        return

    client = await session.scalar(
        select(Client)
        .where(Client.org_id == manager.org_id, Client.slug == slug)
        .options(selectinload(Client.profile))
    )
    if client is None:
        await message.answer(f"Клиент <code>{html.escape(slug)}</code> не найден.")
        return

    profile: ClientProfile | None = client.profile
    name = html.escape(client.name or client.slug)

    if profile is None:
        await message.answer(
            f"📝 Профиль клиента {name} ещё не построен.\n"
            "Он обновляется автоматически по расписанию после нескольких сообщений."
        )
        return

    lines: list[str] = [
        f"📝 <b>Профиль клиента</b> {name} (<code>{html.escape(client.slug)}</code>)",
        "",
    ]
    if profile.summary:
        lines.append(f"<b>Резюме:</b> {html.escape(profile.summary)}")
        lines.append("")
    if profile.pain_points:
        lines.append("<b>Боли:</b>")
        lines.extend(f"• {html.escape(p)}" for p in profile.pain_points)
        lines.append("")
    if profile.objections:
        lines.append("<b>Возражения:</b>")
        lines.extend(f"• {html.escape(p)}" for p in profile.objections)
        lines.append("")
    if profile.won_arguments:
        lines.append("<b>Сильные аргументы:</b>")
        lines.extend(f"• {html.escape(p)}" for p in profile.won_arguments)
        lines.append("")
    if profile.personal_notes:
        lines.append(f"<b>Личные заметки:</b> {html.escape(profile.personal_notes)}")
        lines.append("")
    lines.append(f"<i>Обновлено: {_fmt_dt(profile.updated_at)}</i>")

    await message.answer("\n".join(lines))


@router.message(Command("clients_no_tg"))
async def cmd_clients_no_tg(
    message: TgMessage,
    manager: User,
    session: AsyncSession,
) -> None:
    stmt = (
        select(Client, func.count(MessageModel.id).label("msg_count"))
        .select_from(Client)
        .outerjoin(Conversation, Conversation.client_id == Client.id)
        .outerjoin(MessageModel, MessageModel.conversation_id == Conversation.id)
        .where(
            Client.org_id == manager.org_id,
            Client.telegram_user_id.is_(None),
        )
        .group_by(Client.id)
        .order_by(Client.last_touch_at.is_(None), Client.last_touch_at.desc())
    )
    rows = (await session.execute(stmt)).all()

    if not rows:
        await message.answer("Клиентов вне Telegram пока нет.\n\nСоздать: <code>/add_client</code>")
        return

    lines = ["<b>Клиенты вне Telegram:</b>"]
    for client, msg_count in rows:
        name = html.escape(client.name or client.slug)
        slug = html.escape(client.slug)
        lines.append(
            f"• <code>{slug}</code> — {name} | сообщений: {msg_count} | "
            f"last: {_fmt_dt(client.last_touch_at)}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("remind"))
async def cmd_remind(
    message: TgMessage,
    manager: User,
    session: AsyncSession,
    command: CommandObject,
) -> None:
    parsed = parse_remind_args(command.args or "")
    if isinstance(parsed, str):
        await message.answer(parsed)
        return
    slug, due_at, text = parsed

    client = await session.scalar(
        select(Client).where(Client.org_id == manager.org_id, Client.slug == slug)
    )
    if client is None:
        await message.answer(f"Клиент <code>{html.escape(slug)}</code> не найден.")
        return

    reminder = await create_reminder(
        session,
        client_id=client.id,
        user_id=manager.id,
        due_at=due_at,
        text=text,
    )
    await session.commit()

    name = html.escape(client.name or client.slug)
    await message.answer(
        f"⏰ Напоминание создано: <code>{short_id(reminder.id)}</code>\n"
        f"Клиент: {name}\n"
        f"Когда: <b>{_fmt_dt(reminder.due_at)}</b>\n"
        f"Что: <i>{html.escape(text)}</i>"
    )


@router.message(Command("reminders"))
async def cmd_reminders(
    message: TgMessage,
    manager: User,
    session: AsyncSession,
) -> None:
    pairs = await list_active_reminders(session, user_id=manager.id)
    if not pairs:
        await message.answer("Активных напоминаний нет.")
        return

    lines = ["<b>⏰ Активные напоминания:</b>"]
    for reminder, client in pairs:
        rid = short_id(reminder.id)
        cname = html.escape(client.name or client.slug)
        slug = html.escape(client.slug)
        body = html.escape(reminder_text(reminder))
        marker = "✉️ отправлено" if reminder.status.value == "sent" else "⏳ ожидает"
        lines.append(
            f"• <code>{rid}</code> — {_fmt_dt(reminder.due_at)} | {marker}\n"
            f"  {cname} (<code>{slug}</code>): «{body}»"
        )
    lines.append("\n<code>/done &lt;id&gt;</code> — закрыть напоминание")
    await message.answer("\n".join(lines))


@router.message(Command("done"))
async def cmd_done(
    message: TgMessage,
    manager: User,
    session: AsyncSession,
    command: CommandObject,
) -> None:
    short = (command.args or "").strip()
    if not short:
        await message.answer("Использование: <code>/done &lt;id&gt;</code>")
        return

    result = await find_reminder_by_short_id(session, user_id=manager.id, short_id=short)
    if result is None:
        await message.answer(f"Напоминание <code>{html.escape(short)}</code> не найдено.")
        return
    if result == "ambiguous":
        await message.answer("Несколько напоминаний начинаются на этот префикс. Уточни id.")
        return

    await mark_done(session, result)
    await session.commit()
    await message.answer(f"✅ Закрыто <code>{short_id(result.id)}</code>.")
