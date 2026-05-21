from __future__ import annotations

import asyncio
import html
from typing import Any, cast
from uuid import UUID

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.ai.analyzer import analyze_inbound
from src.ai.profiler import update_profile
from src.ai.responder import suggest_reply
from src.config import settings
from src.db.models import Client, Conversation, MessageDirection, User
from src.db.models import Message as MessageModel
from src.db.session import SessionLocal
from src.services.drafts import channel_for_source, generate_draft_for_message
from src.utils.logger import logger

_ANALYZER_CONTEXT_LIMIT = 10


async def _load_recent_messages(
    session: AsyncSession, conversation_id: UUID, *, limit: int, exclude_id: UUID | None = None
) -> list[MessageModel]:
    stmt = (
        select(MessageModel)
        .where(MessageModel.conversation_id == conversation_id)
        .order_by(MessageModel.sent_at.desc().nulls_last(), MessageModel.created_at.desc())
        .limit(limit + (1 if exclude_id else 0))
    )
    rows = (await session.execute(stmt)).scalars().all()
    if exclude_id is not None:
        rows = [m for m in rows if m.id != exclude_id]
    return list(reversed(rows[:limit]))


def _format_alert(client: Client, msg: MessageModel, analysis: dict[str, Any]) -> str:
    name = html.escape(client.name or client.slug)
    slug = html.escape(client.slug)
    text_preview = html.escape((msg.text or "").strip()[:400])
    suggested = html.escape((analysis.get("suggested_action") or "").strip())
    intent = html.escape(str(analysis.get("intent") or "—"))
    sentiment = html.escape(str(analysis.get("sentiment") or "—"))

    lines = [
        f"🔥 <b>ВЫСОКАЯ срочность</b> от {name} (<code>{slug}</code>)",
        "",
        f"Намерение: <b>{intent}</b> | sentiment: <b>{sentiment}</b>",
    ]
    if text_preview:
        lines.append(f"\nСообщение: <i>{text_preview}</i>")
    if suggested:
        lines.append(f"\nРекомендация: {suggested}")
    lines.append("")
    lines.append(f"<code>/suggest {slug}</code> — варианты ответа")
    lines.append(f"<code>/client {slug}</code> — карточка")
    return "\n".join(lines)


async def process_inbound_message(
    message_id: UUID,
    bot: Bot,
    *,
    update_profile_after: bool = False,
) -> None:
    """AI pipeline for a newly-stored inbound message.

    Runs the Haiku classifier, persists its output to messages.analysis,
    and DMs the manager when urgency=high. If ``update_profile_after`` is
    set, also refreshes the client's profile after analysis — used by
    the manual /note path where every entry is curated input worth
    folding into the profile right away.
    """
    try:
        async with SessionLocal() as session:
            msg = await session.scalar(select(MessageModel).where(MessageModel.id == message_id))
            if msg is None or msg.direction != MessageDirection.IN:
                return
            conversation = await session.scalar(
                select(Conversation).where(Conversation.id == msg.conversation_id)
            )
            if conversation is None:
                return
            client = await session.scalar(select(Client).where(Client.id == conversation.client_id))
            manager = (
                await session.scalar(select(User).where(User.id == conversation.user_id))
                if conversation.user_id is not None
                else None
            )
            if client is None:
                return

            context = await _load_recent_messages(
                session,
                conversation_id=conversation.id,
                limit=_ANALYZER_CONTEXT_LIMIT,
                exclude_id=msg.id,
            )

            analysis = await analyze_inbound(message=msg, context=context)

            msg.analysis = analysis
            await session.commit()

            if analysis.get("urgency") == "high" and manager is not None:
                try:
                    await bot.send_message(
                        chat_id=manager.telegram_user_id,
                        text=_format_alert(client, msg, analysis),
                    )
                except Exception:
                    logger.exception(
                        "Failed to deliver high-urgency alert for client {}", client.slug
                    )

            if update_profile_after:
                try:
                    await update_profile(session, client)
                except Exception:
                    logger.exception(
                        "Profile refresh after manual /note failed for client {}",
                        client.slug,
                    )

        if settings.drafts_autogenerate and channel_for_source(msg.source) is not None:
            asyncio.create_task(generate_draft_for_message(message_id))
    except Exception:
        logger.exception("AI pipeline failed for message {}", message_id)


async def suggest_reply_for_client(
    session: AsyncSession, *, manager: User, slug: str
) -> tuple[Client | None, list[dict[str, str]]]:
    """Resolve a client by slug within the manager's org and generate variants.

    Returns (None, []) when the slug is unknown.
    """

    client = await session.scalar(
        select(Client)
        .where(Client.org_id == manager.org_id, Client.slug == slug)
        .options(selectinload(Client.profile))
    )
    if client is None:
        return None, []

    conversation_id = await _resolve_conversation_id(session, client, manager)
    if conversation_id is None:
        return client, []

    history = await _load_recent_messages(session, conversation_id=conversation_id, limit=30)
    variants = await suggest_reply(
        client=client,
        profile=client.profile,
        history=history,
    )
    return client, variants


async def _resolve_conversation_id(
    session: AsyncSession, client: Client, manager: User
) -> UUID | None:
    """Manager's own conversation with this client; otherwise any conversation on the client."""
    exact = await session.scalar(
        select(Conversation.id).where(
            Conversation.client_id == client.id,
            Conversation.user_id == manager.id,
        )
    )
    if exact is not None:
        return cast("UUID | None", exact)
    return cast(
        "UUID | None",
        await session.scalar(
            select(Conversation.id).where(Conversation.client_id == client.id).limit(1)
        ),
    )
