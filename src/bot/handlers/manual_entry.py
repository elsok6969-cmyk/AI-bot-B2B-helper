from __future__ import annotations

import html
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, cast
from uuid import UUID

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram.types import Message as TgMessage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.ocr import ocr_image
from src.db.models import (
    BusinessType,
    Client,
    ConversationPlatform,
    MessageDirection,
    MessageSource,
    User,
)
from src.db.models import Message as MessageModel
from src.services.ai_pipeline import process_inbound_message
from src.services.clients import (
    create_manual_client,
    resolve_or_create_conversation,
)
from src.utils.bg import spawn
from src.utils.logger import logger

router = Router(name="manual_entry")


# ---------------------------------------------------------------------------
# FSM state definitions
# ---------------------------------------------------------------------------


class AddClientStates(StatesGroup):
    waiting_name = State()
    waiting_business_type = State()
    waiting_volume = State()
    waiting_categories = State()


class NoteStates(StatesGroup):
    waiting_source = State()
    waiting_content = State()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


BUSINESS_TYPE_LABELS: dict[BusinessType, str] = {
    BusinessType.RETAIL: "Розница",
    BusinessType.ONLINE: "Онлайн",
    BusinessType.HORECA: "HoReCa",
    BusinessType.CORP: "Корп.",
    BusinessType.UNKNOWN: "Не знаю",
}

SOURCE_LABELS: dict[MessageSource, str] = {
    MessageSource.MANUAL_SCREENSHOT: "Скрин",
    MessageSource.EMAIL: "Email",
}


def _business_type_keyboard() -> Any:
    kb = InlineKeyboardBuilder()
    for bt in BusinessType:
        kb.button(text=BUSINESS_TYPE_LABELS[bt], callback_data=f"add_client_bt:{bt.value}")
    kb.adjust(3)
    return kb.as_markup()


def _source_keyboard(prefix: str) -> Any:
    kb = InlineKeyboardBuilder()
    for src, label in SOURCE_LABELS.items():
        kb.button(text=label, callback_data=f"{prefix}:{src.value}")
    kb.adjust(2)
    return kb.as_markup()


async def _resolve_client_by_slug(session: AsyncSession, manager: User, slug: str) -> Client | None:
    return cast(
        "Client | None",
        await session.scalar(
            select(Client).where(Client.org_id == manager.org_id, Client.slug == slug)
        ),
    )


# ---------------------------------------------------------------------------
# /cancel — universal exit from any FSM flow
# ---------------------------------------------------------------------------


@router.message(Command("cancel"))
async def cmd_cancel(message: TgMessage, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("Отменено.")


# ---------------------------------------------------------------------------
# /add_client — multi-step form
# ---------------------------------------------------------------------------


@router.message(Command("add_client"))
async def cmd_add_client(message: TgMessage, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AddClientStates.waiting_name)
    await message.answer(
        "Создаём клиента вручную. Введи <b>имя клиента</b> "
        "(имя контакта или название компании).\n\n/cancel — отмена."
    )


@router.message(AddClientStates.waiting_name, F.text)
async def add_client_name(message: TgMessage, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя не может быть пустым. Попробуй ещё раз.")
        return
    if len(name) > 255:
        await message.answer("Слишком длинно. До 255 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(AddClientStates.waiting_business_type)
    await message.answer("Тип бизнеса:", reply_markup=_business_type_keyboard())


@router.callback_query(AddClientStates.waiting_business_type, F.data.startswith("add_client_bt:"))
async def add_client_business_type(callback: CallbackQuery, state: FSMContext) -> None:
    assert callback.data is not None  # enforced by F.data.startswith filter above
    bt_value = callback.data.split(":", 1)[1]
    try:
        bt = BusinessType(bt_value)
    except ValueError:
        await callback.answer("Неизвестный тип.", show_alert=True)
        return
    await state.update_data(business_type=bt.value)
    await state.set_state(AddClientStates.waiting_volume)
    if isinstance(callback.message, TgMessage):
        await callback.message.edit_text(
            f"Тип бизнеса: <b>{html.escape(BUSINESS_TYPE_LABELS[bt])}</b>"
        )
        await callback.message.answer(
            "Оценочный объём заказа в рублях (числом). Можно <code>/skip</code>."
        )
    await callback.answer()


@router.message(AddClientStates.waiting_volume, F.text)
async def add_client_volume(message: TgMessage, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.lower() in {"/skip", "skip", "-", "—"}:
        est_volume: int | None = None
    else:
        cleaned = "".join(ch for ch in raw if ch.isdigit())
        if not cleaned:
            await message.answer("Не понял число. Введи цифрами или /skip.")
            return
        est_volume = int(cleaned)
    await state.update_data(est_volume=est_volume)
    await state.set_state(AddClientStates.waiting_categories)
    await message.answer(
        "Интересующие категории через запятую "
        "(например: <i>парфюм, унисекс, нишевая</i>). Можно <code>/skip</code>."
    )


@router.message(AddClientStates.waiting_categories, F.text)
async def add_client_categories(
    message: TgMessage,
    state: FSMContext,
    manager: User,
    session: AsyncSession,
) -> None:
    raw = (message.text or "").strip()
    if raw.lower() in {"/skip", "skip", "-", "—"}:
        categories: list[str] = []
    else:
        categories = [c.strip() for c in raw.split(",") if c.strip()]

    data = await state.get_data()
    name = data["name"]
    business_type = BusinessType(data.get("business_type", BusinessType.UNKNOWN.value))
    est_volume = data.get("est_volume")

    client = await create_manual_client(
        session,
        manager.org_id,
        name=name,
        owner_user_id=manager.id,
        business_type=business_type,
        est_volume=est_volume,
        interest_categories=categories,
    )
    await session.commit()
    await state.clear()

    name_safe = html.escape(client.name or client.slug)
    slug_safe = html.escape(client.slug)
    cats = ", ".join(html.escape(c) for c in categories) if categories else "—"
    volume_text = f"{est_volume:,}".replace(",", " ") + " ₽" if est_volume else "—"
    await message.answer(
        f"✅ Клиент создан: {name_safe} (<code>{slug_safe}</code>)\n"
        f"Тип: <b>{html.escape(BUSINESS_TYPE_LABELS[business_type])}</b> | "
        f"Объём: <b>{volume_text}</b> | Категории: {cats}\n\n"
        f"Записать сообщение от него: <code>/note {slug_safe}</code>\n"
        f"Записать отправленное: <code>/sent {slug_safe}</code>"
    )


# ---------------------------------------------------------------------------
# /note <slug> — record an inbound (manual) message
# /sent <slug> — record an outbound (manual) message
# ---------------------------------------------------------------------------


async def _start_manual_message(
    *,
    message: TgMessage,
    state: FSMContext,
    manager: User,
    session: AsyncSession,
    command: CommandObject,
    direction: MessageDirection,
) -> None:
    slug = (command.args or "").strip()
    cmd_name = "note" if direction == MessageDirection.IN else "sent"
    if not slug:
        await message.answer(f"Использование: <code>/{cmd_name} &lt;slug&gt;</code>")
        return

    client = await _resolve_client_by_slug(session, manager, slug)
    if client is None:
        await message.answer(
            f"Клиент <code>{html.escape(slug)}</code> не найден. Создать: <code>/add_client</code>."
        )
        return

    await state.clear()
    await state.update_data(
        client_id=str(client.id),
        direction=direction.value,
    )
    await state.set_state(NoteStates.waiting_source)

    prefix = f"manual_src_{cmd_name}"
    prompt = (
        "Откуда сообщение клиента?" if direction == MessageDirection.IN else "Куда ты отправил?"
    )
    await message.answer(
        f"{prompt}\n\nКлиент: <b>{html.escape(client.name or client.slug)}</b>",
        reply_markup=_source_keyboard(prefix),
    )


@router.message(Command("note"))
async def cmd_note(
    message: TgMessage,
    state: FSMContext,
    manager: User,
    session: AsyncSession,
    command: CommandObject,
) -> None:
    await _start_manual_message(
        message=message,
        state=state,
        manager=manager,
        session=session,
        command=command,
        direction=MessageDirection.IN,
    )


@router.message(Command("sent"))
async def cmd_sent(
    message: TgMessage,
    state: FSMContext,
    manager: User,
    session: AsyncSession,
    command: CommandObject,
) -> None:
    await _start_manual_message(
        message=message,
        state=state,
        manager=manager,
        session=session,
        command=command,
        direction=MessageDirection.OUT,
    )


@router.callback_query(
    NoteStates.waiting_source,
    F.data.regexp(r"^manual_src_(note|sent):(manual_screenshot|email)$"),
)
async def on_manual_source(callback: CallbackQuery, state: FSMContext) -> None:
    assert callback.data is not None
    _, src_value = callback.data.split(":", 1)
    source = MessageSource(src_value)
    await state.update_data(source=source.value)
    await state.set_state(NoteStates.waiting_content)

    data = await state.get_data()
    direction = MessageDirection(data["direction"])

    if direction == MessageDirection.IN and source == MessageSource.MANUAL_SCREENSHOT:
        hint = (
            "Отправь скрин сообщения клиента — распознаю текст и сохраню. Можно и просто текстом."
        )
    elif direction == MessageDirection.IN and source == MessageSource.EMAIL:
        hint = "Скопируй сюда текст письма клиента (или скрин)."
    elif direction == MessageDirection.OUT and source == MessageSource.MANUAL_SCREENSHOT:
        hint = "Отправь скрин того, что ты ему написал (или просто текст)."
    else:
        hint = "Скопируй сюда текст письма, которое ты отправил."

    if isinstance(callback.message, TgMessage):
        label = SOURCE_LABELS[source]
        await callback.message.edit_text(f"Источник: <b>{html.escape(label)}</b>")
        await callback.message.answer(hint + "\n\n/cancel — отмена.")
    await callback.answer()


async def _download_largest_photo(bot: Bot, message: TgMessage) -> bytes | None:
    if not message.photo:
        return None
    largest = message.photo[-1]
    buf = BytesIO()
    await bot.download(largest, destination=buf)
    return buf.getvalue()


@router.message(NoteStates.waiting_content)
async def on_manual_content(
    message: TgMessage,
    state: FSMContext,
    manager: User,
    session: AsyncSession,
    bot: Bot,
) -> None:
    data = await state.get_data()
    try:
        client_id = UUID(data["client_id"])
    except (KeyError, ValueError):
        await state.clear()
        await message.answer("Состояние потерялось. Запусти команду заново.")
        return
    direction = MessageDirection(data["direction"])
    source = MessageSource(data["source"])

    client = await session.scalar(select(Client).where(Client.id == client_id))
    if client is None:
        await state.clear()
        await message.answer("Клиент исчез. Запусти команду заново.")
        return

    photo_bytes: bytes | None = None
    text: str = ""

    if message.photo:
        try:
            photo_bytes = await _download_largest_photo(bot, message)
        except Exception:
            logger.exception("Failed to download photo for manual entry")
            await message.answer("❌ Не получилось скачать изображение. Попробуй ещё раз.")
            return
        notice = await message.answer("⏳ Распознаю текст со скрина...")
        try:
            assert photo_bytes is not None
            text = await ocr_image(photo_bytes, "image/jpeg")
        except Exception:
            logger.exception("OCR failed for manual entry")
            await notice.edit_text("❌ Не получилось распознать текст. Попробуй текстом.")
            return
        await notice.delete()
        if message.caption:
            text = f"{text}\n\n[caption] {message.caption.strip()}".strip()
    elif message.text:
        text = message.text.strip()
    else:
        await message.answer("Поддерживается только текст или фото. Попробуй ещё раз или /cancel.")
        return

    conversation = await resolve_or_create_conversation(
        session,
        client_id=client.id,
        user_id=manager.id,
        platform=ConversationPlatform.MANUAL,
    )

    now = datetime.now(UTC)
    raw_payload = message.model_dump(mode="json")
    raw_payload["_manual"] = {
        "via": "note" if direction == MessageDirection.IN else "sent",
        "had_photo": bool(message.photo),
    }
    stored = MessageModel(
        conversation_id=conversation.id,
        direction=direction,
        source=source,
        text=text or None,
        raw_payload=raw_payload,
        sent_at=now,
    )
    session.add(stored)

    client.last_touch_at = now
    if direction == MessageDirection.IN:
        client.last_inbound_at = now
    else:
        client.last_outbound_at = now
    if client.owner_user_id is None:
        client.owner_user_id = manager.id

    await session.commit()
    await state.clear()

    preview = (text[:200] + "…") if len(text) > 200 else text
    preview_html = f"<i>{html.escape(preview)}</i>" if preview else "<i>(пусто)</i>"

    if direction == MessageDirection.IN:
        await message.answer(
            f"📝 Записал входящее от <b>{html.escape(client.name or client.slug)}</b>:\n"
            f"{preview_html}\n\n"
            "Запускаю анализ и обновление профиля..."
        )
        spawn(
            process_inbound_message(stored.id, bot, update_profile_after=True),
            name=f"pipeline:{stored.id}",
        )
    else:
        await message.answer(
            f"✅ Записал отправленное клиенту <b>{html.escape(client.name or client.slug)}</b>:\n"
            f"{preview_html}"
        )
