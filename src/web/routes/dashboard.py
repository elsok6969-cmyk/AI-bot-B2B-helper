from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.db.models import (
    Client,
    Conversation,
    Draft,
    DraftStatus,
    Message,
    MessageDirection,
    Reminder,
    ReminderStatus,
)
from src.web.deps import SessionDep, UserDep, templates

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: SessionDep, user: UserDep) -> HTMLResponse:
    now = datetime.now(UTC)
    last_24h = now - timedelta(hours=24)

    clients_total = await session.scalar(
        select(func.count(Client.id)).where(Client.org_id == user.org_id)
    )
    clients_active_24h = await session.scalar(
        select(func.count(Client.id)).where(
            Client.org_id == user.org_id, Client.last_inbound_at >= last_24h
        )
    )
    inbound_24h = await session.scalar(
        select(func.count(Message.id))
        .join(Message.conversation)
        .join(Conversation.client)
        .where(
            Client.org_id == user.org_id,
            Message.direction == MessageDirection.IN,
            Message.created_at >= last_24h,
        )
    )
    drafts_pending = await session.scalar(
        select(func.count(Draft.id))
        .join(Client, Client.id == Draft.client_id)
        .where(Draft.status == DraftStatus.PENDING, Client.org_id == user.org_id)
    )
    reminders_due = await session.scalar(
        select(func.count(Reminder.id))
        .join(Client, Client.id == Reminder.client_id)
        .where(
            Client.org_id == user.org_id,
            Reminder.status.in_([ReminderStatus.PENDING, ReminderStatus.SENT]),
            Reminder.due_at <= now,
        )
    )

    recent_drafts = (
        (
            await session.execute(
                select(Draft)
                .join(Client, Client.id == Draft.client_id)
                .where(Draft.status == DraftStatus.PENDING, Client.org_id == user.org_id)
                .order_by(Draft.created_at.desc())
                .limit(5)
                .options(selectinload(Draft.client))
            )
        )
        .scalars()
        .all()
    )

    recent_clients = (
        (
            await session.execute(
                select(Client)
                .where(Client.org_id == user.org_id, Client.last_touch_at.is_not(None))
                .order_by(Client.last_touch_at.desc())
                .limit(8)
            )
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": {
                "clients_total": clients_total or 0,
                "clients_active_24h": clients_active_24h or 0,
                "inbound_24h": inbound_24h or 0,
                "drafts_pending": drafts_pending or 0,
                "reminders_due": reminders_due or 0,
            },
            "recent_drafts": recent_drafts,
            "recent_clients": recent_clients,
        },
    )
