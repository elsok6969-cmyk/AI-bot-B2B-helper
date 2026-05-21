from __future__ import annotations

import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from src.config import settings
from src.web.security import COOKIE_NAME, _login_page

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(request: Request, token: str = Form(...)) -> Response:
    expected = settings.web_access_token.get_secret_value().strip()
    if not expected:
        # Auth disabled — just bounce to /.
        return RedirectResponse(url="/", status_code=303)
    if not hmac.compare_digest(token.strip(), expected):
        return _login_page(error="Неверный токен")
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        token.strip(),
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="strict",
        secure=False,  # we're on http://localhost; switching to https? bump this
    )
    return resp


@router.post("/logout")
async def logout() -> Response:
    resp = RedirectResponse(url="/auth/login-page", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/login-page")
async def login_page() -> Response:
    return _login_page()
