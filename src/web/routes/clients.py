from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from src.db.models import (
    BusinessType,
    Client,
    ClientStage,
    ClientTemperature,
    Conversation,
    Draft,
    DraftChannel,
    DraftStatus,
    Message,
    Reminder,
    ReminderStatus,
)
from src.services.channels import send_freeform
from src.web.deps import SessionDep, UserDep, templates
from src.web.flash import attach_flash_to_redirect

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

    # Pending draft counts per client — render an "N черновиков" badge.
    from sqlalchemy import func

    counts_rows = (
        await session.execute(
            select(Draft.client_id, func.count(Draft.id))
            .where(Draft.status == DraftStatus.PENDING)
            .group_by(Draft.client_id)
        )
    ).all()
    pending_by_client = {cid: n for cid, n in counts_rows}

    return templates.TemplateResponse(
        request,
        "clients/list.html",
        {"clients": clients, "q": q or "", "pending_by_client": pending_by_client},
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

    pending_drafts = (
        (
            await session.execute(
                select(Draft)
                .where(Draft.client_id == client.id, Draft.status == DraftStatus.PENDING)
                .order_by(Draft.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "clients/detail.html",
        {
            "client": client,
            "messages": messages_chrono,
            "reminders": reminders,
            "channels": channels,
            "pending_drafts": pending_drafts,
            "stages": [s.value for s in ClientStage],
            "temperatures": [t.value for t in ClientTemperature],
            "business_types": [b.value for b in BusinessType],
        },
    )


@router.post("/{slug}/update")
async def update_client(
    slug: str,
    session: SessionDep,
    user: UserDep,
    name: str = Form(default=""),
    email: str = Form(default=""),
    stage: str = Form(default=""),
    temperature: str = Form(default=""),
    business_type: str = Form(default=""),
) -> RedirectResponse:
    client = await session.scalar(
        select(Client).where(Client.org_id == user.org_id, Client.slug == slug)
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    if name.strip():
        client.name = name.strip()
    client.email = email.strip() or None
    try:
        if stage:
            client.stage = ClientStage(stage)
        if temperature:
            client.temperature = ClientTemperature(temperature)
        if business_type:
            client.business_type = BusinessType(business_type)
    except ValueError as exc:
        resp = RedirectResponse(url=f"/clients/{slug}", status_code=303)
        attach_flash_to_redirect(resp, f"Неверное значение: {exc}", level="error")
        return resp

    await session.commit()
    resp = RedirectResponse(url=f"/clients/{slug}", status_code=303)
    attach_flash_to_redirect(resp, "Карточка обновлена", level="success")
    return resp


@router.post("/{slug}/reminder")
async def create_reminder_route(
    slug: str,
    session: SessionDep,
    user: UserDep,
    when: str = Form(...),
    text: str = Form(...),
) -> RedirectResponse:
    from src.services.reminders import create_reminder, parse_when

    client = await session.scalar(
        select(Client).where(Client.org_id == user.org_id, Client.slug == slug)
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    due_at = parse_when(when)
    resp = RedirectResponse(url=f"/clients/{slug}", status_code=303)
    if due_at is None:
        attach_flash_to_redirect(
            resp,
            "Не понял время. Примеры: завтра 10:00, через 3 дня, пятница 14:00.",
            level="error",
        )
        return resp
    await create_reminder(
        session, client_id=client.id, user_id=user.id, due_at=due_at, text=text.strip()
    )
    await session.commit()
    attach_flash_to_redirect(resp, f"Напоминание создано на {due_at:%d.%m %H:%M}", level="success")
    return resp


@router.post("/{slug}/note")
async def add_note_route(
    slug: str,
    session: SessionDep,
    user: UserDep,
    text: str = Form(...),
    direction: str = Form(default="in"),
) -> RedirectResponse:
    from src.db.models import (
        ConversationPlatform,
        MessageDirection,
        MessageSource,
    )
    from src.db.models import (
        Message as MessageModel,
    )
    from src.services.clients import resolve_or_create_conversation

    client = await session.scalar(
        select(Client).where(Client.org_id == user.org_id, Client.slug == slug)
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    text = text.strip().replace("\x00", "")
    resp = RedirectResponse(url=f"/clients/{slug}", status_code=303)
    if not text:
        attach_flash_to_redirect(resp, "Текст заметки пуст", level="error")
        return resp

    dir_enum = MessageDirection.IN if direction == "in" else MessageDirection.OUT
    convo = await resolve_or_create_conversation(
        session,
        client_id=client.id,
        user_id=user.id,
        platform=ConversationPlatform.MANUAL,
    )
    msg = MessageModel(
        conversation_id=convo.id,
        direction=dir_enum,
        source=MessageSource.MANUAL_TEXT,
        text=text,
    )
    session.add(msg)
    await session.commit()
    attach_flash_to_redirect(resp, "Заметка добавлена", level="success")
    return resp


@router.post("/{slug}/refresh-profile")
async def refresh_profile(
    slug: str, session: SessionDep, user: UserDep
) -> RedirectResponse:
    from src.ai.profiler import update_profile

    client = await session.scalar(
        select(Client).where(Client.org_id == user.org_id, Client.slug == slug)
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    resp = RedirectResponse(url=f"/clients/{slug}", status_code=303)
    try:
        await update_profile(session, client)
        await session.commit()
        attach_flash_to_redirect(resp, "Профиль обновлён", level="success")
    except Exception as exc:
        attach_flash_to_redirect(resp, f"Не удалось обновить профиль: {exc}", level="error")
    return resp


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

    resp = RedirectResponse(url=f"/clients/{slug}", status_code=303)

    # "note" is a pseudo-channel for note-only clients (no TG, no email):
    # we just record the text as a MANUAL_TEXT message and bail.
    if channel == "note":
        return await add_note_route(slug, session, user, text=text, direction="out")

    try:
        ch = DraftChannel(channel)
    except ValueError:
        attach_flash_to_redirect(resp, f"Неизвестный канал: {channel}", level="error")
        return resp

    # ChannelSendError → flash banner via global handler.
    await send_freeform(
        session,
        user=user,
        client=client,
        channel=ch,
        text=text,
        subject=subject,
    )
    attach_flash_to_redirect(resp, "Сообщение отправлено", level="success")
    return resp
