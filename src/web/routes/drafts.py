from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.db.models import Client, Draft, DraftStatus
from src.services.channels import send_draft
from src.services.drafts import reject
from src.web.deps import SessionDep, UserDep, templates
from src.web.flash import attach_flash_to_redirect

router = APIRouter(prefix="/drafts", tags=["drafts"])


@router.get("", response_class=HTMLResponse)
async def list_drafts(
    request: Request, session: SessionDep, user: UserDep, status: str = "pending"
) -> HTMLResponse:
    try:
        status_enum = DraftStatus(status)
    except ValueError:
        status_enum = DraftStatus.PENDING

    stmt = (
        select(Draft)
        .join(Client, Client.id == Draft.client_id)
        .where(Draft.status == status_enum, Client.org_id == user.org_id)
        .options(
            selectinload(Draft.client),
            selectinload(Draft.source_message),
        )
        .order_by(Draft.created_at.desc())
        .limit(200)
    )
    drafts = (await session.execute(stmt)).scalars().all()
    return templates.TemplateResponse(
        request,
        "drafts/list.html",
        {"drafts": drafts, "status": status_enum.value},
    )


@router.post("/{draft_id}/send")
async def send_draft_route(
    draft_id: UUID, session: SessionDep, text: str = Form(...)
) -> RedirectResponse:
    # ChannelSendError is converted to a flash banner by the global handler.
    await send_draft(session, draft_id, text_override=text)
    resp = RedirectResponse(url="/drafts", status_code=303)
    attach_flash_to_redirect(resp, "Сообщение отправлено", level="success")
    return resp


@router.post("/{draft_id}/reject")
async def reject_draft_route(draft_id: UUID, session: SessionDep) -> RedirectResponse:
    await reject(session, draft_id)
    return RedirectResponse(url="/drafts", status_code=303)


@router.post("/{draft_id}/select")
async def select_variant(
    draft_id: UUID, session: SessionDep, text: str = Form(...)
) -> RedirectResponse:
    draft = await session.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.selected_text = text
    await session.commit()
    return RedirectResponse(url="/drafts", status_code=303)
