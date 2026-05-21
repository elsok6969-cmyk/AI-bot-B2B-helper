from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from src.db.models import (
    Client,
    Conversation,
    DraftChannel,
    Message,
    Reminder,
    ReminderStatus,
)
from src.services.channels import ChannelSendError, send_freeform
from src.web.deps import SessionDep, UserDep, templates

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_class=HTMLResponse)
async def list_clients(
    request: Request,
    session: SessionDep,
    user: UserDep,
    q: str | None = None,
) -> HTMLResponse:
    stmt = select(Client).where(Client.org_id == user.org_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                Client.slug.ilike(like),
                Client.name.ilike(like),
                Client.telegram_username.ilike(like),
                Client.email.ilike(like),
            )
        )
    stmt = stmt.order_by(Client.last_touch_at.desc().nulls_last(), Client.created_at.desc())
    clients = (await session.execute(stmt)).scalars().all()
    return templates.TemplateResponse(
        request, "clients/list.html", {"clients": clients, "q": q or ""}
    )


@router.get("/{slug}", response_class=HTMLResponse)
async def client_detail(
    slug: str, request: Request, session: SessionDep, user: UserDep
) -> HTMLResponse:
    client = await session.scalar(
        select(Client)
        .where(Client.org_id == user.org_id, Client.slug == slug)
        .options(selectinload(Client.profile))
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    messages = (
        (
            await session.execute(
                select(Message)
                .join(Message.conversation)
                .where(Conversation.client_id == client.id)
                .order_by(Message.sent_at.desc().nulls_last(), Message.created_at.desc())
                .limit(80)
            )
        )
        .scalars()
        .all()
    )
    messages_chrono = list(reversed(list(messages)))

    reminders = (
        (
            await session.execute(
                select(Reminder)
                .where(
                    Reminder.client_id == client.id,
                    Reminder.status.in_([ReminderStatus.PENDING, ReminderStatus.SENT]),
                )
                .order_by(Reminder.due_at)
            )
        )
        .scalars()
        .all()
    )

    channels: list[tuple[str, str]] = []
    if client.telegram_user_id is not None:
        channels.append((DraftChannel.TELETHON_USER.value, "Telegram (Telethon)"))
    if client.email:
        channels.append((DraftChannel.EMAIL.value, f"Email ({client.email})"))
    if not channels:
        channels.append(("note", "Только заметка"))

    return templates.TemplateResponse(
        request,
        "clients/detail.html",
        {
            "client": client,
            "messages": messages_chrono,
            "reminders": reminders,
            "channels": channels,
        },
    )


@router.post("/{slug}/send")
async def send_message_route(
    slug: str,
    session: SessionDep,
    user: UserDep,
    channel: str = Form(...),
    text: str = Form(...),
    subject: str | None = Form(default=None),
) -> RedirectResponse:
    client = await session.scalar(
        select(Client).where(Client.org_id == user.org_id, Client.slug == slug)
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    try:
        ch = DraftChannel(channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {channel}") from exc

    try:
        await send_freeform(
            session,
            user=user,
            client=client,
            channel=ch,
            text=text,
            subject=subject,
        )
    except ChannelSendError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url=f"/clients/{slug}", status_code=303)
