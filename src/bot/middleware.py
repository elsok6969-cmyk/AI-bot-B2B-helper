from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy import select

from src.db.models import User
from src.db.session import SessionLocal


class DbSessionMiddleware(BaseMiddleware):
    """Open an async DB session per update, expose it as data['session']."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with SessionLocal() as session:
            data["session"] = session
            return await handler(event, data)


class AuthMiddleware(BaseMiddleware):
    """Resolve the sender to an organization User. Ignore unknown senders.

    Attached to command routers only — business updates resolve the
    manager themselves via business_connection_id.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return None

        session = data["session"]
        user = await session.scalar(
            select(User).where(User.telegram_user_id == from_user.id)
        )
        if user is None:
            return None

        data["manager"] = user
        return await handler(event, data)
