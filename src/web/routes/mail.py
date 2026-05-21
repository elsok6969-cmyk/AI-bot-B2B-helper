from __future__ import annotations

import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from src.config import settings
from src.db.models import Mailbox
from src.services.crypto import SecretsKeyMissingError, encrypt, is_configured
from src.web.deps import SessionDep, UserDep, templates
from src.web.flash import attach_flash_to_redirect

# Yandex app-password shape: 16 lowercase letters (sometimes shown in
# groups of 4 separated by spaces). Anything else is almost certainly
# the user's main account password, which we don't want.
_YANDEX_APP_PASSWORD_RE = re.compile(r"^[a-z]{16}$")


def _looks_like_app_password(p: str) -> bool:
    stripped = p.replace(" ", "").replace("-", "")
    return bool(_YANDEX_APP_PASSWORD_RE.fullmatch(stripped))

router = APIRouter(prefix="/mail", tags=["mail"])


@router.get("", response_class=HTMLResponse)
async def mail_index(
    request: Request, session: SessionDep, user: UserDep
) -> HTMLResponse:
    mailbox = await session.scalar(select(Mailbox).where(Mailbox.user_id == user.id))
    return templates.TemplateResponse(
        request,
        "mail/index.html",
        {
            "mailbox": mailbox,
            "defaults": {
                "imap_host": settings.mail_imap_host,
                "imap_port": settings.mail_imap_port,
                "smtp_host": settings.mail_smtp_host,
                "smtp_port": settings.mail_smtp_port,
            },
            "secrets_ready": is_configured(),
        },
    )


@router.post("/save")
async def mail_save(
    session: SessionDep,
    user: UserDep,
    email: str = Form(...),
    password: str = Form(default=""),
    imap_host: str = Form(default="imap.yandex.ru"),
    imap_port: int = Form(default=993),
    smtp_host: str = Form(default="smtp.yandex.ru"),
    smtp_port: int = Form(default=465),
    is_active: bool = Form(default=False),
) -> RedirectResponse:
    if not is_configured():
        resp = RedirectResponse(url="/mail", status_code=303)
        attach_flash_to_redirect(
            resp,
            "SECRETS_KEY не настроен. Добавь его в .env и перезапусти.",
            level="error",
        )
        return resp

    # Reject internal/loopback IMAP/SMTP hosts up front — same SSRF defence
    # as in the runtime, but surface as a clean flash on save.
    from src.integrations.mail_runtime import MailError, assert_safe_host

    for label, host in (("IMAP", imap_host), ("SMTP", smtp_host)):
        try:
            assert_safe_host(host)
        except MailError as exc:
            resp = RedirectResponse(url="/mail", status_code=303)
            attach_flash_to_redirect(resp, f"{label}: {exc}", level="error")
            return resp

    mailbox = await session.scalar(select(Mailbox).where(Mailbox.user_id == user.id))
    password = (password or "").strip()

    # Empty password on update = keep existing. Empty on create = error.
    if not password and mailbox is None:
        resp = RedirectResponse(url="/mail", status_code=303)
        attach_flash_to_redirect(resp, "Пароль обязателен при первом сохранении.", level="error")
        return resp

    encrypted = mailbox.password_encrypted if mailbox is not None else None
    if password:
        # Yandex disabled IMAP-with-main-password — warn the user.
        if not _looks_like_app_password(password) and "yandex" in imap_host:
            resp = RedirectResponse(url="/mail", status_code=303)
            attach_flash_to_redirect(
                resp,
                "Похоже, ты ввёл обычный пароль Яндекса. Нужен пароль приложения "
                "(16 строчных букв) — https://yandex.ru/support/id/authorization/app-passwords.html",
                level="error",
            )
            return resp
        try:
            encrypted = encrypt(password)
        except SecretsKeyMissingError as exc:
            resp = RedirectResponse(url="/mail", status_code=303)
            attach_flash_to_redirect(resp, str(exc), level="error")
            return resp

    if mailbox is None:
        mailbox = Mailbox(
            user_id=user.id,
            email=email,
            password_encrypted=encrypted or "",
            imap_host=imap_host,
            imap_port=imap_port,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            is_active=is_active,
        )
        session.add(mailbox)
    else:
        mailbox.email = email
        if encrypted is not None:
            mailbox.password_encrypted = encrypted
        mailbox.imap_host = imap_host
        mailbox.imap_port = imap_port
        mailbox.smtp_host = smtp_host
        mailbox.smtp_port = smtp_port
        mailbox.is_active = is_active

    await session.commit()
    resp = RedirectResponse(url="/mail", status_code=303)
    attach_flash_to_redirect(resp, "Настройки почты сохранены", level="success")
    return resp


@router.post("/poll")
async def mail_poll_now(session: SessionDep, user: UserDep) -> RedirectResponse:
    from src.integrations.mail_runtime import poll_all_mailboxes
    from src.scheduler.context import get_bot
    from src.utils.bg import spawn

    try:
        bot = get_bot()
    except RuntimeError:
        bot = None
    spawn(poll_all_mailboxes(bot), name="manual_mail_poll")

    resp = RedirectResponse(url="/mail", status_code=303)
    attach_flash_to_redirect(resp, "Опрос почты запущен в фоне. Обнови страницу через минуту.", level="info")
    return resp


@router.post("/delete")
async def mail_delete(session: SessionDep, user: UserDep) -> RedirectResponse:
    mailbox = await session.scalar(select(Mailbox).where(Mailbox.user_id == user.id))
    if mailbox is not None:
        await session.delete(mailbox)
        await session.commit()
    return RedirectResponse(url="/mail", status_code=303)
