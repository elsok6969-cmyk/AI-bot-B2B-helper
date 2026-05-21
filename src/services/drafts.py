from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.ai.responder import suggest_reply
from src.db.models import (
    Client,
    Conversation,
    Draft,
    DraftChannel,
    DraftStatus,
    Message,
    MessageDirection,
    MessageSource,
)
from src.db.session import SessionLocal
from src.utils.logger import logger

_HISTORY_LIMIT = 30


_SOURCE_TO_CHANNEL: dict[MessageSource, DraftChannel] = {
    MessageSource.TG_BUSINESS: DraftChannel.TG_BUSINESS,
    MessageSource.TELETHON_USER: DraftChannel.TELETHON_USER,
    MessageSource.EMAIL: DraftChannel.EMAIL,
}


def channel_for_source(source: MessageSource) -> DraftChannel | None:
    return _SOURCE_TO_CHANNEL.get(source)


async def _recent_history(
    session: AsyncSession, conversation_id: UUID, *, limit: int
) -> list[Message]:
    rows = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sent_at.desc().nulls_last(), Message.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(reversed(list(rows)))


async def generate_draft_for_message(message_id: UUID) -> None:
    """Run the responder on a freshly-stored inbound message and persist a Draft.

    Called as a fire-and-forget task after an inbound is ingested
    (Telegram bot, Telethon, or email). Never raises — logs on failure.
    """
    try:
        async with SessionLocal() as session:
            msg = await session.scalar(
                select(Message).where(Message.id == message_id)
            )
            if msg is None or msg.direction != MessageDirection.IN:
                return
            channel = channel_for_source(msg.source)
            if channel is None:
                return

            conversation = await session.scalar(
                select(Conversation).where(Conversation.id == msg.conversation_id)
            )
            if conversation is None:
                return

            client = await session.scalar(
                select(Client)
                .where(Client.id == conversation.client_id)
                .options(selectinload(Client.profile))
            )
            if client is None:
                return

            history = await _recent_history(
                session, conversation_id=conversation.id, limit=_HISTORY_LIMIT
            )

            variants = await suggest_reply(
                client=client,
                profile=client.profile,
                history=history,
            )
            if not variants:
                logger.info("No draft variants generated for message {}", message_id)
                return

            subject: str | None = None
            if channel == DraftChannel.EMAIL and msg.raw_payload:
                orig_subj = msg.raw_payload.get("subject")
                if orig_subj:
                    subject = (
                        orig_subj if orig_subj.lower().startswith("re:") else f"Re: {orig_subj}"
                    )

            draft = Draft(
                client_id=client.id,
                conversation_id=conversation.id,
                user_id=conversation.user_id,
                source_message_id=msg.id,
                channel=channel,
                status=DraftStatus.PENDING,
                variants=variants,
                selected_text=variants[0]["text"] if variants else None,
                subject=subject,
            )
            session.add(draft)
            await session.commit()
            logger.info(
                "Draft created (id={}, client={}, channel={})",
                draft.id,
                client.slug,
                channel.value,
            )
    except Exception:
        logger.exception("Failed to generate draft for message {}", message_id)


async def mark_sent(session: AsyncSession, draft_id: UUID) -> None:
    draft = await session.get(Draft, draft_id)
    if draft is None:
        return
    draft.status = DraftStatus.SENT
    draft.sent_at = datetime.now(UTC)
    await session.commit()


async def reject(session: AsyncSession, draft_id: UUID) -> None:
    draft = await session.get(Draft, draft_id)
    if draft is None:
        return
    draft.status = DraftStatus.REJECTED
    await session.commit()
