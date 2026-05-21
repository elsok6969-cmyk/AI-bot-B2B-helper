from sqlalchemy import select

from src.config import settings
from src.db.models import Organization, User, UserRole
from src.db.session import SessionLocal
from src.utils.logger import logger

OWNER_ORG_NAME = "Mynota"


async def ensure_owner() -> None:
    """Idempotently ensure that the owner org + user exist."""
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(User).where(User.telegram_user_id == settings.owner_telegram_id)
        )
        if existing is not None:
            return

        org = await session.scalar(select(Organization).where(Organization.name == OWNER_ORG_NAME))
        if org is None:
            org = Organization(name=OWNER_ORG_NAME)
            session.add(org)
            await session.flush()
            logger.info("Seeded organization {} (id={})", OWNER_ORG_NAME, org.id)

        user = User(
            org_id=org.id,
            telegram_user_id=settings.owner_telegram_id,
            name="Owner",
            role=UserRole.OWNER,
        )
        session.add(user)
        await session.commit()
        logger.info(
            "Seeded owner user (id={}, telegram_user_id={})",
            user.id,
            settings.owner_telegram_id,
        )
