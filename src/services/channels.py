from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import (
    Client,
    Draft,
    DraftChannel,
    Mailbox,
    Message,
    MessageDirection,
    MessageSource,
    TelethonAccount,
    User,
)
from src.services.drafts import mark_sent
from src.utils.logger import logger


class ChannelSendError(RuntimeError):
    """Raised when a channel adapter fails to send a message."""


# Outbound caps. Telegram hard-limits a message to 4096 chars; SMTP has
# no real limit but a 100k body is already a foot-gun (someone pasted a
# log dump). We truncate with a clear marker rather than silently failing.
_MAX_LEN_TG = 4096
_MAX_LEN_EMAIL = 100_000


def _safe_text(text: str, *, channel: DraftChannel) -> str:
    """Strip NUL bytes (Postgres rejects them) and cap to channel limits."""
    text = text.replace("\x00", "")
    cap = _MAX_LEN_EMAIL if channel == DraftChannel.EMAIL else _MAX_LEN_TG
    if len(text) > cap:
        text = text[: cap - 20] + "\n…(обрезано)"
    return text


async def _store_outbound(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    client_id: UUID,
    text: str,
    source: MessageSource,
    raw_payload: dict | None = None,
) -> Message:
    now = datetime.now(UTC)
    msg = Message(
        conversation_id=conversation_id,
        direction=MessageDirection.OUT,
        source=source,
        text=text,
        sent_at=now,
        raw_payload=raw_payload,
    )
    session.add(msg)
    client = await session.get(Client, client_id)
    if client is not None:
        client.last_touch_at = now
        client.last_outbound_at = now
    await session.flush()
    return msg


async def send_draft(
    session: AsyncSession, draft_id: UUID, *, text_override: str | None = None
) -> None:
    """Send a draft via its channel adapter and store the outbound message.

    Acquires a row-level lock on the draft so two simultaneous clicks
    only send once. Skips if already sent/rejected. Raises
    ChannelSendError on failure; caller should surface to the user
    without flipping the draft to SENT.
    """
    from src.db.models import DraftStatus

    draft = await session.scalar(
        select(Draft)
        .where(Draft.id == draft_id)
        .options(
            selectinload(Draft.client),
            selectinload(Draft.conversation),
            selectinload(Draft.user),
        )
        .with_for_update()
    )
    if draft is None:
        raise ChannelSendError(f"Draft {draft_id} not found")
    if draft.status != DraftStatus.PENDING:
        # Already handled — make the operation idempotent. The previous
        # request will have flipped status to SENT.
        return

    text = (text_override or draft.selected_text or "").strip()
    if not text:
        raise ChannelSendError("Draft has no text to send")
    text = _safe_text(text, channel=draft.channel)

    client = draft.client
    if client is None:
        raise ChannelSendError("Draft is missing client")

    if draft.channel == DraftChannel.TELETHON_USER:
        from src.integrations.telethon_runtime import send_message as telethon_send

        if draft.user_id is None:
            raise ChannelSendError("Draft has no owning user for Telethon send")
        if client.telegram_user_id is None:
            raise ChannelSendError("Client has no telegram_user_id")

        account = await session.scalar(
            select(TelethonAccount).where(TelethonAccount.user_id == draft.user_id)
        )
        if account is None or not account.is_authorized:
            raise ChannelSendError("Telethon account is not authorized")

        await telethon_send(
            user_id=draft.user_id,
            peer_id=client.telegram_user_id,
            text=text,
        )
        await _store_outbound(
            session,
            conversation_id=draft.conversation_id,
            client_id=client.id,
            text=text,
            source=MessageSource.TELETHON_USER,
        )

    elif draft.channel == DraftChannel.EMAIL:
        from src.integrations.mail_runtime import send_email

        if draft.user_id is None:
            raise ChannelSendError("Draft has no owning user for email send")
        if not client.email:
            raise ChannelSendError("Client has no email address")

        mailbox = await session.scalar(
            select(Mailbox).where(Mailbox.user_id == draft.user_id, Mailbox.is_active.is_(True))
        )
        if mailbox is None:
            raise ChannelSendError("No active mailbox configured for this user")

        subject = draft.subject or "Re:"
        await send_email(
            mailbox=mailbox,
            to_address=client.email,
            subject=subject,
            body=text,
        )
        await _store_outbound(
            session,
            conversation_id=draft.conversation_id,
            client_id=client.id,
            text=text,
            source=MessageSource.EMAIL,
            raw_payload={"to": client.email, "subject": subject, "from": mailbox.email},
        )

    elif draft.channel == DraftChannel.TG_BUSINESS:
        # Telegram Business API doesn't allow bots to send on a manager's
        # behalf outside of a business_connection context. We don't attempt
        # this from web — the manager replies in their own Telegram client
        # and our handler captures it. Mark the draft as sent so it leaves
        # the queue; record the outbound for history.
        await _store_outbound(
            session,
            conversation_id=draft.conversation_id,
            client_id=client.id,
            text=text,
            source=MessageSource.MANUAL_TEXT,
            raw_payload={"note": "marked sent via web; manager sent manually in Telegram"},
        )

    else:  # pragma: no cover
        raise ChannelSendError(f"Unknown channel {draft.channel}")

    await mark_sent(session, draft_id)
    logger.info("Sent draft {} via {}", draft_id, draft.channel.value)


async def send_freeform(
    session: AsyncSession,
    *,
    user: User,
    client: Client,
    channel: DraftChannel,
    text: str,
    subject: str | None = None,
) -> Message:
    """Send a free-form (non-draft) message from the web UI.

    Mirrors send_draft but for ad-hoc outbound — used when the user wants
    to write a custom reply without picking from AI variants.
    """
    text = text.strip()
    if not text:
        raise ChannelSendError("Empty message")
    text = _safe_text(text, channel=channel)

    from src.db.models import ConversationPlatform
    from src.services.clients import resolve_or_create_conversation

    platform = (
        ConversationPlatform.EMAIL
        if channel == DraftChannel.EMAIL
        else ConversationPlatform.TELEGRAM
    )
    conversation = await resolve_or_create_conversation(
        session, client_id=client.id, user_id=user.id, platform=platform
    )

    if channel == DraftChannel.TELETHON_USER:
        from src.integrations.telethon_runtime import send_message as telethon_send

        account = await session.scalar(
            select(TelethonAccount).where(TelethonAccount.user_id == user.id)
        )
        if account is None or not account.is_authorized:
            raise ChannelSendError("Telethon account is not authorized")
        if client.telegram_user_id is None:
            raise ChannelSendError("Client has no telegram_user_id")
        await telethon_send(user_id=user.id, peer_id=client.telegram_user_id, text=text)
        msg = await _store_outbound(
            session,
            conversation_id=conversation.id,
            client_id=client.id,
            text=text,
            source=MessageSource.TELETHON_USER,
        )
    elif channel == DraftChannel.EMAIL:
        from src.integrations.mail_runtime import send_email

        mailbox = await session.scalar(
            select(Mailbox).where(Mailbox.user_id == user.id, Mailbox.is_active.is_(True))
        )
        if mailbox is None:
            raise ChannelSendError("No active mailbox")
        if not client.email:
            raise ChannelSendError("Client has no email")
        subj = subject or "Сообщение"
        await send_email(
            mailbox=mailbox, to_address=client.email, subject=subj, body=text
        )
        msg = await _store_outbound(
            session,
            conversation_id=conversation.id,
            client_id=client.id,
            text=text,
            source=MessageSource.EMAIL,
            raw_payload={"to": client.email, "subject": subj, "from": mailbox.email},
        )
    else:
        msg = await _store_outbound(
            session,
            conversation_id=conversation.id,
            client_id=client.id,
            text=text,
            source=MessageSource.MANUAL_TEXT,
            raw_payload={"note": "logged from web; sent manually in Telegram"},
        )

    await session.commit()
    return msg
