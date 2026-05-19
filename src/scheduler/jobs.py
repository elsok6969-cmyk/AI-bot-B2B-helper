from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.ai.profiler import update_profile
from src.db.models import Client, ClientProfile
from src.db.session import SessionLocal
from src.utils.logger import logger

_STALE_AFTER = timedelta(hours=23)


async def daily_profile_update() -> None:
    """Refresh profiles for clients with recent inbound activity.

    Runs once a day via APScheduler. Skips clients whose profile was
    updated within the last 23 hours so a re-run won't duplicate work,
    and skips clients with no last_inbound_at (no real activity yet).
    """
    started = datetime.now(UTC)
    logger.info("daily_profile_update started")

    async with SessionLocal() as session:
        clients = (
            await session.execute(
                select(Client).where(Client.last_inbound_at.is_not(None))
            )
        ).scalars().all()

        refreshed = 0
        skipped = 0
        for client in clients:
            existing = await session.scalar(
                select(ClientProfile).where(ClientProfile.client_id == client.id)
            )
            if existing is not None and (started - existing.updated_at) < _STALE_AFTER:
                skipped += 1
                continue
            try:
                await update_profile(session, client)
                refreshed += 1
            except Exception:
                logger.exception("Profile update failed for client {}", client.slug)

    logger.info(
        "daily_profile_update finished: refreshed={} skipped={}", refreshed, skipped
    )
