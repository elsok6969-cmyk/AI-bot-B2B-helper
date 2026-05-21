"""FastAPI dependencies — DB session, current user, templates."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.session import SessionLocal

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db)]


async def get_default_user(session: SessionDep) -> User:
    """Return the first owner (no auth — local-only deployment)."""
    user = await session.scalar(select(User).order_by(User.created_at).limit(1))
    if user is None:
        raise HTTPException(
            status_code=503,
            detail="No users in DB yet — start the bot and send /start once.",
        )
    return user


UserDep = Annotated[User, Depends(get_default_user)]


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"
