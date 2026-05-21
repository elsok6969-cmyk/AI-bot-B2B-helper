"""Runtime registry for live Telethon clients, one per authorized manager.

Two surfaces:
- ``ensure_listening(user_id)`` / ``stop_listening(user_id)`` — lifecycle
  used by the worker and by the login flow once a session is captured.
- ``login_*`` helpers — the two-step phone → code → (password) flow,
  driven by the web UI.
- ``send_message(user_id, peer_id, text)`` — outbound used by
  ``services.channels``.

All public functions are async and safe to call from any task.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession
from telethon.tl.custom import Message as TelethonMessage

from src.config import settings
from src.db.models import (
    ConversationPlatform,
    MessageDirection,
    MessageSource,
    TelethonAccount,
    User,
)
from src.db.models import (
    Message as MessageModel,
)
from src.db.session import SessionLocal
from src.services.ai_pipeline import process_inbound_message
from src.services.clients import resolve_or_create_client, resolve_or_create_conversation
from src.services.crypto import decrypt, encrypt
from src.utils.bg import spawn
from src.utils.logger import logger


class TelethonError(RuntimeError):
    pass


@dataclass
class _PendingLogin:
    client: TelegramClient
    phone: str
    phone_code_hash: str


_pending: dict[UUID, _PendingLogin] = {}
_active: dict[UUID, TelegramClient] = {}
_locks: dict[UUID, asyncio.Lock] = {}
_main_bot = None  # type: ignore[assignment]


def set_bot(bot: Any) -> None:
    """Wire the aiogram Bot so high-urgency alerts still reach the manager."""
    global _main_bot
    _main_bot = bot


def _lock_for(user_id: UUID) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock


def _build_client(session_string: str = "") -> TelegramClient:
    if not settings.telethon_api_id or not settings.telethon_api_hash.get_secret_value():
        raise TelethonError(
            "TELETHON_API_ID / TELETHON_API_HASH are not configured. "
            "Get them at https://my.telegram.org and put them in .env."
        )
    return TelegramClient(
        StringSession(session_string),
        settings.telethon_api_id,
        settings.telethon_api_hash.get_secret_value(),
    )


# ---------- login flow ----------------------------------------------------


async def login_start(user_id: UUID, phone: str) -> None:
    """Step 1 of login — send the verification code to the phone."""
    async with _lock_for(user_id):
        pending = _pending.pop(user_id, None)
        if pending is not None:
            with contextlib.suppress(Exception):
                await pending.client.disconnect()

        client = _build_client()
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
        except PhoneNumberInvalidError as exc:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise TelethonError("Telegram отказал в коде: номер не валиден.") from exc
        except FloodWaitError as exc:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise TelethonError(
                f"Слишком много попыток. Подожди {exc.seconds} сек и попробуй снова."
            ) from exc
        _pending[user_id] = _PendingLogin(
            client=client, phone=phone, phone_code_hash=sent.phone_code_hash
        )

        async with SessionLocal() as session:
            account = await session.scalar(
                select(TelethonAccount).where(TelethonAccount.user_id == user_id)
            )
            if account is None:
                account = TelethonAccount(user_id=user_id)
                session.add(account)
            account.phone = phone
            account.login_phase = "code_sent"
            account.phone_code_hash = sent.phone_code_hash
            await session.commit()


async def login_submit_code(user_id: UUID, code: str, password: str | None = None) -> bool:
    """Step 2 — submit the SMS/app code (and 2FA password if needed).

    Returns True on success, False if a 2FA password is required and was
    not provided. Raises TelethonError on bad code / expired code.
    """
    async with _lock_for(user_id):
        pending = _pending.get(user_id)
        if pending is None:
            raise TelethonError("Login session expired — start over.")

        try:
            try:
                await pending.client.sign_in(
                    phone=pending.phone,
                    code=code,
                    phone_code_hash=pending.phone_code_hash,
                )
            except SessionPasswordNeededError:
                if not password:
                    async with SessionLocal() as session:
                        account = await session.scalar(
                            select(TelethonAccount).where(TelethonAccount.user_id == user_id)
                        )
                        if account is not None:
                            account.login_phase = "password_needed"
                            await session.commit()
                    return False
                try:
                    await pending.client.sign_in(password=password)
                except PasswordHashInvalidError as exc:
                    raise TelethonError("Неверный 2FA-пароль. Попробуй ещё раз.") from exc
            except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
                raise TelethonError(f"Код не подошёл: {exc}") from exc
            except FloodWaitError as exc:
                raise TelethonError(
                    f"Telegram попросил подождать {exc.seconds} сек."
                ) from exc

            me = await pending.client.get_me()
            session_string = pending.client.session.save()

            async with SessionLocal() as session:
                account = await session.scalar(
                    select(TelethonAccount).where(TelethonAccount.user_id == user_id)
                )
                if account is None:
                    account = TelethonAccount(user_id=user_id)
                    session.add(account)
                account.session_encrypted = encrypt(session_string)
                account.is_authorized = True
                account.login_phase = "idle"
                account.phone_code_hash = None
                account.telegram_user_id = me.id if me else None
                account.last_login_at = datetime.now(UTC)
                await session.commit()

            _active[user_id] = pending.client
            _pending.pop(user_id, None)
            await _attach_handlers(user_id, pending.client)
            return True
        except Exception:
            # Don't disconnect the client — user may retry the code.
            raise


async def logout(user_id: UUID) -> None:
    async with _lock_for(user_id):
        client = _active.pop(user_id, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.log_out()
            with contextlib.suppress(Exception):
                await client.disconnect()
        pending = _pending.pop(user_id, None)
        if pending is not None:
            with contextlib.suppress(Exception):
                await pending.client.disconnect()
        async with SessionLocal() as session:
            account = await session.scalar(
                select(TelethonAccount).where(TelethonAccount.user_id == user_id)
            )
            if account is not None:
                account.is_authorized = False
                account.session_encrypted = None
                account.login_phase = "idle"
                account.phone_code_hash = None
                await session.commit()


# ---------- worker lifecycle ---------------------------------------------


async def ensure_listening(user_id: UUID) -> None:
    """Connect (or reconnect) the stored session and attach the listener."""
    async with _lock_for(user_id):
        if user_id in _active and _active[user_id].is_connected():
            return
        async with SessionLocal() as session:
            account = await session.scalar(
                select(TelethonAccount).where(TelethonAccount.user_id == user_id)
            )
            if account is None or not account.is_authorized or not account.session_encrypted:
                return
            session_string = decrypt(account.session_encrypted)

        client = _build_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("Telethon session for user {} is no longer authorized", user_id)
            await client.disconnect()
            async with SessionLocal() as session:
                acc = await session.scalar(
                    select(TelethonAccount).where(TelethonAccount.user_id == user_id)
                )
                if acc is not None:
                    acc.is_authorized = False
                    await session.commit()
            return
        _active[user_id] = client
        await _attach_handlers(user_id, client)
        logger.info("Telethon listener up for user {}", user_id)


async def stop_listening(user_id: UUID) -> None:
    async with _lock_for(user_id):
        client = _active.pop(user_id, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()


async def shutdown_all() -> None:
    for user_id in list(_active.keys()):
        await stop_listening(user_id)
    for pending in list(_pending.values()):
        with contextlib.suppress(Exception):
            await pending.client.disconnect()
    _pending.clear()


# ---------- send ----------------------------------------------------------


async def send_message(*, user_id: UUID, peer_id: int, text: str) -> None:
    client = _active.get(user_id)
    if client is None or not client.is_connected():
        await ensure_listening(user_id)
        client = _active.get(user_id)
    if client is None:
        raise TelethonError("Telethon client not available for this user")
    await client.send_message(peer_id, text)


# ---------- event handler ------------------------------------------------


async def _attach_handlers(user_id: UUID, client: TelegramClient) -> None:
    me = await client.get_me()
    self_id = me.id if me else None

    @client.on(events.NewMessage(incoming=True))
    async def _on_incoming(event: events.NewMessage.Event) -> None:  # type: ignore[no-untyped-def]
        try:
            await _ingest(user_id, event.message, self_id, MessageDirection.IN)
        except Exception:
            logger.exception("Telethon incoming ingest failed")

    @client.on(events.NewMessage(outgoing=True))
    async def _on_outgoing(event: events.NewMessage.Event) -> None:  # type: ignore[no-untyped-def]
        try:
            await _ingest(user_id, event.message, self_id, MessageDirection.OUT)
        except Exception:
            logger.exception("Telethon outgoing ingest failed")


async def _ingest(
    manager_user_id: UUID,
    tg_msg: TelethonMessage,
    self_id: int | None,
    direction: MessageDirection,
) -> None:
    # Only private chats (1-on-1) for now — groups would explode the client roster.
    if not tg_msg.is_private:
        return

    peer = await tg_msg.get_chat()
    if peer is None or getattr(peer, "bot", False):
        return

    # Peer for IN is the sender; for OUT it's the chat partner.
    if direction == MessageDirection.IN:
        peer_id = tg_msg.sender_id
        sender = await tg_msg.get_sender()
        first_name = getattr(sender, "first_name", None) if sender else None
        last_name = getattr(sender, "last_name", None) if sender else None
        username = getattr(sender, "username", None) if sender else None
    else:
        peer_id = tg_msg.chat_id
        first_name = getattr(peer, "first_name", None)
        last_name = getattr(peer, "last_name", None)
        username = getattr(peer, "username", None)

    if peer_id is None or peer_id == self_id:
        return

    async with SessionLocal() as session:
        manager = await session.get(User, manager_user_id)
        if manager is None:
            return

        client = await resolve_or_create_client(
            session,
            manager.org_id,
            telegram_user_id=peer_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
        )
        if client.owner_user_id is None:
            client.owner_user_id = manager.id

        conversation = await resolve_or_create_conversation(
            session,
            client_id=client.id,
            user_id=manager.id,
            platform=ConversationPlatform.TELEGRAM,
        )

        # Dedupe: Telethon can re-deliver an event after a reconnect.
        # raw_payload.id is the Telegram message id, unique per peer.
        from sqlalchemy import select as _select

        existing_id = await session.scalar(
            _select(MessageModel.id).where(
                MessageModel.conversation_id == conversation.id,
                MessageModel.source == MessageSource.TELETHON_USER,
                MessageModel.raw_payload["id"].astext == str(tg_msg.id),
            )
        )
        if existing_id is not None:
            return

        text = (tg_msg.message or "").replace("\x00", "") or None
        stored = MessageModel(
            conversation_id=conversation.id,
            direction=direction,
            source=MessageSource.TELETHON_USER,
            text=text,
            raw_payload={
                "id": tg_msg.id,
                "peer_id": peer_id,
                "out": direction == MessageDirection.OUT,
            },
            sent_at=tg_msg.date,
        )
        session.add(stored)
        client.last_touch_at = tg_msg.date
        if direction == MessageDirection.IN:
            client.last_inbound_at = tg_msg.date
        else:
            client.last_outbound_at = tg_msg.date
        await session.commit()

        if direction == MessageDirection.IN and _main_bot is not None:
            spawn(
                process_inbound_message(stored.id, _main_bot),
                name=f"pipeline:{stored.id}",
            )


# ---------- worker entry --------------------------------------------------


async def start_worker() -> None:
    """Bring up listeners for every authorized account at boot."""
    if not settings.telethon_enabled:
        logger.info("Telethon worker disabled via TELETHON_ENABLED=false")
        return
    try:
        from src.services.crypto import is_configured

        if not is_configured():
            logger.warning("Telethon worker: SECRETS_KEY not set, skipping startup")
            return

        # Reset half-finished login flows from the previous process —
        # the in-memory _pending dict is gone, so the next code-submit
        # would otherwise raise "Login session expired" with no UI hint.
        async with SessionLocal() as session:
            stale = await session.execute(
                select(TelethonAccount).where(TelethonAccount.login_phase != "idle")
            )
            for acc in stale.scalars():
                acc.login_phase = "idle"
                acc.phone_code_hash = None
            await session.commit()

            ids = (
                await session.execute(
                    select(TelethonAccount.user_id).where(TelethonAccount.is_authorized.is_(True))
                )
            ).scalars().all()
        for uid in ids:
            try:
                await ensure_listening(uid)
            except Exception:
                logger.exception("Failed to start Telethon listener for user {}", uid)
        logger.info("Telethon worker started ({} accounts)", len(ids))
    except Exception:
        logger.exception("Telethon worker startup failed")


