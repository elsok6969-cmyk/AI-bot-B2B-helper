from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from src.config import settings
from src.db.models import TelethonAccount
from src.integrations.telethon_runtime import (
    TelethonError,
    ensure_listening,
    login_start,
    login_submit_code,
    logout,
)
from src.services.crypto import is_configured
from src.web.deps import SessionDep, UserDep, templates

router = APIRouter(prefix="/telethon", tags=["telethon"])


@router.get("", response_class=HTMLResponse)
async def telethon_status(
    request: Request, session: SessionDep, user: UserDep
) -> HTMLResponse:
    account = await session.scalar(
        select(TelethonAccount).where(TelethonAccount.user_id == user.id)
    )
    api_ready = bool(settings.telethon_api_id and settings.telethon_api_hash.get_secret_value())
    return templates.TemplateResponse(
        request,
        "telethon/status.html",
        {
            "account": account,
            "api_ready": api_ready,
            "secrets_ready": is_configured(),
            "telethon_enabled": settings.telethon_enabled,
        },
    )


@router.post("/login/start")
async def telethon_login_start(
    user: UserDep, phone: str = Form(...)
) -> RedirectResponse:
    try:
        await login_start(user.id, phone.strip())
    except TelethonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/telethon", status_code=303)


@router.post("/login/code")
async def telethon_login_code(
    user: UserDep,
    code: str = Form(...),
    password: str | None = Form(default=None),
) -> RedirectResponse:
    try:
        ok = await login_submit_code(user.id, code.strip(), password)
    except TelethonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ok:
        # 2FA password requested; render status again so user enters it
        return RedirectResponse(url="/telethon", status_code=303)
    await ensure_listening(user.id)
    return RedirectResponse(url="/telethon", status_code=303)


@router.post("/logout")
async def telethon_logout_route(user: UserDep) -> RedirectResponse:
    await logout(user.id)
    return RedirectResponse(url="/telethon", status_code=303)
