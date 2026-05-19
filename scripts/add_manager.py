"""Add a manager to the Mynota organization.

Usage (inside the container):

    docker compose exec app python -m scripts.add_manager <telegram_user_id> "<name>" [--role owner|manager]

Defaults: role=manager. Idempotent: an existing user with the same
telegram_user_id is left alone.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from src.db.models import Organization, User, UserRole
from src.db.session import SessionLocal
from src.utils.logger import logger, setup_logger


async def add_manager(telegram_user_id: int, name: str, role: UserRole) -> None:
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if existing is not None:
            print(
                f"user already exists: id={existing.id} name={existing.name!r} "
                f"role={existing.role.value}"
            )
            return

        org = await session.scalar(select(Organization).order_by(Organization.created_at))
        if org is None:
            raise SystemExit(
                "No organization found. Start the bot at least once so the owner "
                "is seeded, then add managers."
            )

        user = User(
            org_id=org.id,
            telegram_user_id=telegram_user_id,
            name=name,
            role=role,
        )
        session.add(user)
        await session.commit()
        print(f"created user: id={user.id} name={user.name!r} role={user.role.value}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("telegram_user_id", type=int)
    parser.add_argument("name", type=str)
    parser.add_argument(
        "--role",
        choices=["owner", "manager"],
        default="manager",
    )
    args = parser.parse_args()

    setup_logger("INFO")
    logger.info(
        "Adding manager telegram_user_id={} name={} role={}",
        args.telegram_user_id,
        args.name,
        args.role,
    )
    asyncio.run(add_manager(args.telegram_user_id, args.name, UserRole(args.role)))


if __name__ == "__main__":
    main()
