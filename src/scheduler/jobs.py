from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta

from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select

from src.ai.profiler import update_profile
from src.db.models import Client, ClientProfile, ReminderStatus, User
from src.db.session import SessionLocal
from src.scheduler.context import get_bot
from src.services.digest_service import collect_digest, format_digest_html
from src.services.reminders import find_due_reminders, reminder_text, short_id
from src.utils.logger import logger

_PROFILE_STALE_AFTER = timedelta(hours=23)
_PROFILE_ACTIVE_WINDOW = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Daily digest
# ---------------------------------------------------------------------------


async def daily_digest_job() -> None:
    """Send a per-manager digest in DM. Runs once a day."""
    bot = get_bot()
    logger.info("daily_digest_job started")

    async with SessionLocal() as session:
        managers = (await session.execute(select(User).order_by(User.created_at))).scalars().all()

        sent = 0
        skipped = 0
        for manager in managers:
            try:
                digest = await collect_digest(session, manager)
            except Exception:
                logger.exception("Failed to collect digest for manager {}", manager.id)
                continue
            if digest.is_empty():
                skipped += 1
                continue
            try:
                await bot.send_message(
                    chat_id=manager.telegram_user_id,
                    text=format_digest_html(digest),
                )
                sent += 1
            except TelegramAPIError:
                logger.exception(
                    "Failed to deliver digest to manager {} (tg_id={})",
                    manager.id,
                    manager.telegram_user_id,
                )

    logger.info("daily_digest_job finished: sent={} skipped={}", sent, skipped)


# ---------------------------------------------------------------------------
# Reminder dispatch
# ---------------------------------------------------------------------------


def _format_reminder_alert(reminder, client) -> str:
    text = html.escape(reminder_text(reminder) or "(без описания)")
    name = html.escape(client.name or client.slug)
    slug = html.escape(client.slug)
    rid = short_id(reminder.id)
    return (
        "⏰ <b>Напоминание</b>\n\n"
        f"Клиент: {name} (<code>{slug}</code>)\n"
        f"Что: <i>{text}</i>\n\n"
        f"<code>/done {rid}</code> — закрыть\n"
        f"<code>/client {slug}</code> — карточка"
    )


async def check_reminders_job() -> None:
    """Deliver due reminders to managers; mark them as SENT."""
    bot = get_bot()
    async with SessionLocal() as session:
        due = await find_due_reminders(session)
        if not due:
            return

        delivered = 0
        failed = 0
        for reminder, client, manager in due:
            try:
                await bot.send_message(
                    chat_id=manager.telegram_user_id,
                    text=_format_reminder_alert(reminder, client),
                )
                reminder.status = ReminderStatus.SENT
                delivered += 1
            except TelegramAPIError:
                logger.exception(
                    "Failed to deliver reminder {} to manager {}",
                    reminder.id,
                    manager.id,
                )
                failed += 1
                continue

        if delivered:
            await session.commit()
        if delivered or failed:
            logger.info("check_reminders_job: delivered={} failed={}", delivered, failed)


# ---------------------------------------------------------------------------
# Nightly profile refresh
# ---------------------------------------------------------------------------


async def nightly_profile_refresh() -> None:
    """Refresh profiles for clients active in the last 24 hours.

    Skips clients whose profile was updated less than 23 hours ago so a
    re-run won't duplicate work. Replaces the earlier daily_profile_update
    that ran across all clients with last_inbound_at set.
    """
    started = datetime.now(UTC)
    active_cutoff = started - _PROFILE_ACTIVE_WINDOW
    logger.info("nightly_profile_refresh started")

    async with SessionLocal() as session:
        clients = (
            (
                await session.execute(
                    select(Client).where(
                        Client.last_inbound_at.is_not(None),
                        Client.last_inbound_at >= active_cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )

        refreshed = 0
        skipped = 0
        for client in clients:
            existing = await session.scalar(
                select(ClientProfile).where(ClientProfile.client_id == client.id)
            )
            if existing is not None and (started - existing.updated_at) < _PROFILE_STALE_AFTER:
                skipped += 1
                continue
            try:
                await update_profile(session, client)
                refreshed += 1
            except Exception:
                logger.exception("Profile refresh failed for client {}", client.slug)

    logger.info("nightly_profile_refresh finished: refreshed={} skipped={}", refreshed, skipped)
