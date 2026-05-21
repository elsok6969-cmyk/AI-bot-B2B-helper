"""FastAPI dependencies — DB session, current user, templates."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User, UserRole
from src.db.session import SessionLocal

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Pin autoescape explicitly — don't rely on the implicit default. Stored
# values from Telegram/email are user-controlled, so any rendering must
# escape, period. Future contributors: do not use `|safe` on dynamic data.
templates.env.autoescape = select_autoescape(
    enabled_extensions=("html", "htm", "xml"),
    default_for_string=True,
)


class NoOwnerError(Exception):
    """Raised when the DB has no users yet — handled by a global handler
    that renders the onboarding/welcome page instead of a 500."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db)]


async def get_default_user(session: SessionDep) -> User:
    """Return the org owner. Falls back to the first user if no owner
    is flagged (older deployments). Raises NoOwnerError on empty DB —
    a global handler in app.py renders the welcome page.
    """
    user = await session.scalar(
        select(User).where(User.role == UserRole.OWNER).order_by(User.created_at).limit(1)
    )
    if user is None:
        user = await session.scalar(select(User).order_by(User.created_at).limit(1))
    if user is None:
        raise NoOwnerError()
    return user


UserDep = Annotated[User, Depends(get_default_user)]


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def render_welcome() -> HTMLResponse:
    """Onboarding page shown when no owner exists in the DB yet.

    Two flavors: if BOT_TOKEN is still empty (first-run, never set up),
    point the user at /setup; otherwise they configured the bot but
    haven't sent /start yet — show the original "DM your bot" copy.
    """
    from src.config import settings

    bot_ready = bool(settings.bot_token.get_secret_value().strip())
    tpl = "welcome.html" if bot_ready else "welcome_setup.html"
    body = (_TEMPLATES_DIR / tpl).read_text(encoding="utf-8")
    return HTMLResponse(body, status_code=200)
