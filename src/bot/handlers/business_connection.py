from aiogram import Router
from aiogram.types import BusinessConnection
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import BusinessConnection as BusinessConnectionModel
from src.db.models import User
from src.utils.logger import logger

router = Router(name="business_connection")


def _extract_can_reply(event: BusinessConnection) -> bool:
    rights = getattr(event, "rights", None)
    if rights is not None and getattr(rights, "can_reply", None) is not None:
        return bool(rights.can_reply)
    legacy = getattr(event, "can_reply", None)
    return bool(legacy) if legacy is not None else False


@router.business_connection()
async def on_business_connection(
    event: BusinessConnection,
    session: AsyncSession,
) -> None:
    user = await session.scalar(
        select(User).where(User.telegram_user_id == event.user.id)
    )
    if user is None:
        logger.warning(
            "BusinessConnection from unregistered user (tg_id={}, connection_id={})",
            event.user.id,
            event.id,
        )
        return

    can_reply = _extract_can_reply(event)
    bc = await session.scalar(
        select(BusinessConnectionModel).where(
            BusinessConnectionModel.user_id == user.id,
            BusinessConnectionModel.connection_id == event.id,
        )
    )
    if bc is None:
        bc = BusinessConnectionModel(
            user_id=user.id,
            connection_id=event.id,
            is_enabled=event.is_enabled,
            can_reply=can_reply,
        )
        session.add(bc)
        logger.info(
            "Business connection established for user {} (connection_id={}, enabled={}, can_reply={})",
            user.id,
            event.id,
            event.is_enabled,
            can_reply,
        )
    else:
        bc.is_enabled = event.is_enabled
        bc.can_reply = can_reply
        logger.info(
            "Business connection updated for user {} (connection_id={}, enabled={}, can_reply={})",
            user.id,
            event.id,
            event.is_enabled,
            can_reply,
        )

    await session.commit()
