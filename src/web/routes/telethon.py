from __future__ import annotations

import re

from fastapi import APIRouter, Form, Request
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
from src.web.flash import attach_flash_to_redirect

_PHONE_RE = re.compile(r"^\+\d{8,15}$")

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
    phone = phone.strip().replace(" ", "").replace("-", "")
    resp = RedirectResponse(url="/telethon", status_code=303)
    if not _PHONE_RE.match(phone):
        attach_flash_to_redirect(
            resp,
            "Номер должен быть в формате +<код страны><номер>, только цифры. Пример: +79991234567",
            level="error",
        )
        return resp
    try:
        await login_start(user.id, phone)
    except TelethonError as exc:
        attach_flash_to_redirect(resp, str(exc), level="error")
        return resp
    attach_flash_to_redirect(resp, f"Код отправлен на {phone}. Введи его ниже.", level="info")
    return resp


@router.post("/login/code")
async def telethon_login_code(
    user: UserDep,
    code: str = Form(...),
    password: str | None = Form(default=None),
) -> RedirectResponse:
    resp = RedirectResponse(url="/telethon", status_code=303)
    try:
        ok = await login_submit_code(user.id, code.strip(), password)
    except TelethonError as exc:
        attach_flash_to_redirect(resp, str(exc), level="error")
        return resp
    if not ok:
        attach_flash_to_redirect(
            resp, "Нужен ещё 2FA-пароль cloud password. Введи его и попробуй снова.", level="info"
        )
        return resp
    await ensure_listening(user.id)
    attach_flash_to_redirect(resp, "Telethon подключён. Чаты будут подтягиваться сами.", level="success")
    return resp


@router.post("/logout")
async def telethon_logout_route(user: UserDep) -> RedirectResponse:
    await logout(user.id)
    resp = RedirectResponse(url="/telethon", status_code=303)
    attach_flash_to_redirect(resp, "Telethon отключён.", level="info")
    return resp
