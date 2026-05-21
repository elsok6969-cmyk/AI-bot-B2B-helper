"""IMAP poller + SMTP sender for Yandex Mail (or any IMAPS/SMTPS mailbox).

The poller is invoked from APScheduler every ``mail_poll_interval_minutes``;
it walks every active ``Mailbox``, fetches unseen messages whose UID is
greater than ``last_uid_seen``, persists them as inbound Messages tied to
the matching Client (matched on the sender's email), and kicks off the
AI pipeline so drafts get generated.

SMTP sending is exposed as ``send_email`` and used by ``services.channels``.
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import re
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from typing import Any
from uuid import UUID

import aioimaplib
import aiosmtplib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import (
    Client,
    ConversationPlatform,
    Mailbox,
    MessageDirection,
    MessageSource,
    User,
)
from src.db.models import (
    Message as MessageModel,
)
from src.db.session import SessionLocal
from src.services.ai_pipeline import process_inbound_message
from src.services.clients import resolve_or_create_conversation
from src.services.crypto import decrypt, is_configured
from src.utils.logger import logger
from src.utils.slug import slugify


class MailError(RuntimeError):
    pass


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_email(value: str | None) -> bool:
    return bool(value and _EMAIL_RE.match(value.strip()))


# ---------- IMAP poller ---------------------------------------------------


async def poll_all_mailboxes(bot: Any = None) -> None:
    """Scan every active mailbox once. Safe to call from APScheduler."""
    if not settings.mail_enabled:
        return
    if not is_configured():
        logger.warning("Mail poller: SECRETS_KEY not set, skipping")
        return
    async with SessionLocal() as session:
        mailboxes = (
            await session.execute(select(Mailbox).where(Mailbox.is_active.is_(True)))
        ).scalars().all()
    for mb in mailboxes:
        try:
            await _poll_one(mb, bot)
        except Exception:
            logger.exception("IMAP poll failed for {}", mb.email)


async def _poll_one(mailbox: Mailbox, bot: Any) -> None:
    password = decrypt(mailbox.password_encrypted)
    client = aioimaplib.IMAP4_SSL(host=mailbox.imap_host, port=mailbox.imap_port, timeout=30)
    await client.wait_hello_from_server()
    try:
        await client.login(mailbox.email, password)
        await client.select("INBOX")

        # Fetch UIDs strictly greater than last_uid_seen.
        search_criteria = f"UID {mailbox.last_uid_seen + 1}:*"
        status, data = await client.uid_search(search_criteria)
        if status != "OK":
            logger.warning("UID SEARCH failed for {}: {}", mailbox.email, data)
            return
        if not data or not data[0]:
            await _touch_polled(mailbox.id)
            return
        raw_uids = data[0].split() if isinstance(data[0], (bytes, str)) else data[0]
        uid_list = []
        for u in raw_uids:
            if isinstance(u, bytes):
                u = u.decode()
            try:
                uid_int = int(u)
            except (TypeError, ValueError):
                continue
            if uid_int > mailbox.last_uid_seen:
                uid_list.append(uid_int)
        uid_list.sort()
        if not uid_list:
            await _touch_polled(mailbox.id)
            return

        highest_uid = mailbox.last_uid_seen
        for uid in uid_list:
            try:
                status, msg_data = await client.uid("fetch", str(uid), "(RFC822)")
                if status != "OK":
                    continue
                raw_bytes = _extract_raw_body(msg_data)
                if raw_bytes is None:
                    continue
                parsed = email.message_from_bytes(raw_bytes)
                await _store_inbound_email(mailbox, parsed, uid, bot)
                highest_uid = max(highest_uid, uid)
            except Exception:
                logger.exception("Failed to ingest IMAP UID {} from {}", uid, mailbox.email)

        if highest_uid > mailbox.last_uid_seen:
            async with SessionLocal() as session:
                fresh = await session.get(Mailbox, mailbox.id)
                if fresh is not None:
                    fresh.last_uid_seen = highest_uid
                    fresh.last_polled_at = datetime.now(UTC)
                    await session.commit()
        else:
            await _touch_polled(mailbox.id)
    finally:
        with contextlib.suppress(Exception):
            await client.logout()


async def _touch_polled(mailbox_id: UUID) -> None:
    async with SessionLocal() as session:
        fresh = await session.get(Mailbox, mailbox_id)
        if fresh is not None:
            fresh.last_polled_at = datetime.now(UTC)
            await session.commit()


def _extract_raw_body(fetch_data: Any) -> bytes | None:
    """aioimaplib returns a mixed list; find the bytes blob with RFC822 body."""
    if not fetch_data:
        return None
    for item in fetch_data:
        if isinstance(item, bytes) and len(item) > 50 and b"\n" in item:
            return item
    return None


def _decode_text(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        for enc in (part.get_content_charset(), "utf-8", "cp1251", "latin-1"):
            if not enc:
                continue
            try:
                return payload.decode(enc, errors="replace")
            except (LookupError, UnicodeDecodeError):
                continue
        return payload.decode("utf-8", errors="replace")
    return str(payload)


def _extract_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype == "text/plain" and "attachment" not in disp:
                return _decode_text(part).strip()
        # fallback to HTML, strip very crudely
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html_body = _decode_text(part)
                return re.sub(r"<[^>]+>", "", html_body).strip()
        return ""
    return _decode_text(msg).strip()


async def _store_inbound_email(
    mailbox: Mailbox, parsed: email.message.Message, uid: int, bot: Any
) -> None:
    from_addr = parseaddr(parsed.get("From", ""))[1].strip().lower()
    if not from_addr:
        return
    # Skip self-sent.
    if from_addr == mailbox.email.lower():
        return

    subject = (parsed.get("Subject") or "").strip()
    text_body = _extract_text(parsed)
    received_at = _parse_date(parsed.get("Date"))

    to_addrs = [a[1].lower() for a in getaddresses([parsed.get("To", "")]) if a[1]]
    cc_addrs = [a[1].lower() for a in getaddresses([parsed.get("Cc", "")]) if a[1]]

    async with SessionLocal() as session:
        manager = await session.get(User, mailbox.user_id)
        if manager is None:
            return

        client = await _resolve_or_create_email_client(session, manager.org_id, from_addr)
        if client.owner_user_id is None:
            client.owner_user_id = manager.id

        conversation = await resolve_or_create_conversation(
            session,
            client_id=client.id,
            user_id=manager.id,
            platform=ConversationPlatform.EMAIL,
        )

        stored = MessageModel(
            conversation_id=conversation.id,
            direction=MessageDirection.IN,
            source=MessageSource.EMAIL,
            text=text_body or None,
            raw_payload={
                "uid": uid,
                "subject": subject,
                "from": from_addr,
                "to": to_addrs,
                "cc": cc_addrs,
                "message_id": parsed.get("Message-ID"),
            },
            sent_at=received_at,
        )
        session.add(stored)
        client.last_touch_at = received_at or datetime.now(UTC)
        client.last_inbound_at = client.last_touch_at
        await session.commit()
        message_id = stored.id

    if bot is not None:
        asyncio.create_task(process_inbound_message(message_id, bot))


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


async def _resolve_or_create_email_client(
    session: AsyncSession, org_id: UUID, email_addr: str
) -> Client:
    email_addr = email_addr.lower().strip()
    client = await session.scalar(
        select(Client).where(Client.org_id == org_id, Client.email == email_addr)
    )
    if client is not None:
        return client

    local = email_addr.split("@")[0]
    base_slug = slugify(local)
    # Find a free slug.
    candidate = base_slug
    n = 2
    while await session.scalar(
        select(Client.id).where(Client.org_id == org_id, Client.slug == candidate)
    ):
        candidate = f"{base_slug}-{n}"
        n += 1

    client = Client(
        org_id=org_id,
        telegram_user_id=None,
        telegram_username=None,
        email=email_addr,
        name=local,
        slug=candidate,
    )
    session.add(client)
    await session.flush()
    return client


# ---------- SMTP sender --------------------------------------------------


async def send_email(*, mailbox: Mailbox, to_address: str, subject: str, body: str) -> None:
    if not looks_like_email(to_address):
        raise MailError(f"Invalid recipient email: {to_address}")
    password = decrypt(mailbox.password_encrypted)

    msg = EmailMessage()
    msg["From"] = mailbox.email
    msg["To"] = to_address
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body, charset="utf-8")

    await aiosmtplib.send(
        msg,
        hostname=mailbox.smtp_host,
        port=mailbox.smtp_port,
        username=mailbox.email,
        password=password,
        use_tls=True,
        timeout=30,
    )
