from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from src.config import settings
from src.db.models import Mailbox
from src.services.crypto import SecretsKeyMissingError, encrypt, is_configured
from src.web.deps import SessionDep, UserDep, templates

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
    password: str = Form(...),
    imap_host: str = Form(default="imap.yandex.ru"),
    imap_port: int = Form(default=993),
    smtp_host: str = Form(default="smtp.yandex.ru"),
    smtp_port: int = Form(default=465),
    is_active: bool = Form(default=False),
) -> RedirectResponse:
    if not is_configured():
        raise HTTPException(
            status_code=400,
            detail="SECRETS_KEY is not configured. Add it to .env and restart.",
        )
    try:
        encrypted = encrypt(password)
    except SecretsKeyMissingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    mailbox = await session.scalar(select(Mailbox).where(Mailbox.user_id == user.id))
    if mailbox is None:
        mailbox = Mailbox(
            user_id=user.id,
            email=email,
            password_encrypted=encrypted,
            imap_host=imap_host,
            imap_port=imap_port,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            is_active=is_active,
        )
        session.add(mailbox)
    else:
        mailbox.email = email
        mailbox.password_encrypted = encrypted
        mailbox.imap_host = imap_host
        mailbox.imap_port = imap_port
        mailbox.smtp_host = smtp_host
        mailbox.smtp_port = smtp_port
        mailbox.is_active = is_active

    await session.commit()
    return RedirectResponse(url="/mail", status_code=303)


@router.post("/poll")
async def mail_poll_now(session: SessionDep, user: UserDep) -> RedirectResponse:
    from src.integrations.mail_runtime import poll_all_mailboxes

    await poll_all_mailboxes(None)
    return RedirectResponse(url="/mail", status_code=303)


@router.post("/delete")
async def mail_delete(session: SessionDep, user: UserDep) -> RedirectResponse:
    mailbox = await session.scalar(select(Mailbox).where(Mailbox.user_id == user.id))
    if mailbox is not None:
        await session.delete(mailbox)
        await session.commit()
    return RedirectResponse(url="/mail", status_code=303)
