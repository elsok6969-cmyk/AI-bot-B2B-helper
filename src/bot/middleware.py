from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from src.db.models import User
from src.db.session import SessionLocal
from src.utils.logger import logger


def _manager_chat_id_from_update(event: TelegramObject) -> int | None:
    """Extract the manager-facing chat id from an outer-middleware event.

    Returns None for business updates (their chat is the client's) and for
    anything that isn't a direct message or callback query.
    """
    update = event if isinstance(event, Update) else None
    if update is None:
        return None
    msg = update.message
    if msg is not None and msg.business_connection_id is None and msg.chat is not None:
        return msg.chat.id
    cb = update.callback_query
    if cb is not None and cb.message is not None and cb.message.chat is not None:
        return cb.message.chat.id
    return None


class DbSessionMiddleware(BaseMiddleware):
    """Open an async DB session per update; convert DB-down to a friendly reply."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            async with SessionLocal() as session:
                data["session"] = session
                return await handler(event, data)
        except OperationalError as exc:
            logger.error("DB unavailable: {}", exc)
            bot: Bot | None = data.get("bot")
            chat_id = _manager_chat_id_from_update(event)
            if bot is not None and chat_id is not None:
                try:
                    await bot.send_message(
                        chat_id,
                        "⚠️ БД временно недоступна. Попробуй чуть позже.",
                    )
                except Exception:
                    logger.exception("Failed to notify manager about DB-down")
            return None


class AuthMiddleware(BaseMiddleware):
    """Resolve the sender to an organization User. Ignore unknown senders.

    Attached to dp.message and dp.callback_query so commands and FSM flows
    have ``manager`` injected. Business updates route through their own
    observer and resolve the manager via BusinessConnection.connection_id.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.business_connection_id is not None:
            return await handler(event, data)

        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return None
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        session = data.get("session")
        if session is None:
            return await handler(event, data)

        user = await session.scalar(select(User).where(User.telegram_user_id == from_user.id))
        if user is None:
            return None

        data["manager"] = user
        return await handler(event, data)
