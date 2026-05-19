import asyncio

from aiogram import Bot, Router
from aiogram.types import Message as TgMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import BusinessConnection as BusinessConnectionModel
from src.db.models import Message as MessageModel
from src.db.models import MessageDirection, MessageSource
from src.services.ai_pipeline import process_inbound_message
from src.services.clients import resolve_or_create_client, resolve_or_create_conversation
from src.utils.logger import logger

router = Router(name="business_messages")


@router.business_message()
async def on_business_message(message: TgMessage, session: AsyncSession, bot: Bot) -> None:
    if not message.business_connection_id:
        logger.warning("business_message without business_connection_id; skipping")
        return

    bc = await session.scalar(
        select(BusinessConnectionModel)
        .where(BusinessConnectionModel.connection_id == message.business_connection_id)
        .options(selectinload(BusinessConnectionModel.user))
    )
    if bc is None or bc.user is None:
        logger.warning(
            "business_message for unknown connection_id={}", message.business_connection_id
        )
        return

    manager = bc.user

    is_from_manager = (
        message.from_user is not None and message.from_user.id == manager.telegram_user_id
    )
    direction = MessageDirection.OUT if is_from_manager else MessageDirection.IN

    if direction == MessageDirection.IN and message.from_user is not None:
        client_tg_id = message.from_user.id
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name
        username = message.from_user.username
    else:
        client_tg_id = message.chat.id
        first_name = message.chat.first_name
        last_name = message.chat.last_name
        username = message.chat.username

    client = await resolve_or_create_client(
        session,
        manager.org_id,
        telegram_user_id=client_tg_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
    )
    if client.owner_user_id is None:
        client.owner_user_id = manager.id

    conversation = await resolve_or_create_conversation(
        session, client_id=client.id, user_id=manager.id
    )

    stored = MessageModel(
        conversation_id=conversation.id,
        direction=direction,
        source=MessageSource.TG_BUSINESS,
        text=message.text or message.caption,
        raw_payload=message.model_dump(mode="json"),
        sent_at=message.date,
    )
    session.add(stored)

    client.last_touch_at = message.date
    if direction == MessageDirection.IN:
        client.last_inbound_at = message.date
    else:
        client.last_outbound_at = message.date

    await session.commit()

    logger.debug(
        "Stored {} message for client {} (msg_id={})",
        direction.value,
        client.slug,
        stored.id,
    )

    if direction == MessageDirection.IN:
        asyncio.create_task(process_inbound_message(stored.id, bot))
