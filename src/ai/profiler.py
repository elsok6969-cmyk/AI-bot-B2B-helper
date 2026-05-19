from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.ai.client import ai_client, extract_tool_input
from src.ai.prompts import PROFILER_SYSTEM_PROMPT
from src.config import settings
from src.db.models import Client, ClientProfile, Conversation, MessageDirection
from src.db.models import Message as MessageModel
from src.utils.logger import logger

_MAX_HISTORY = 50

_PROFILER_TOOL: dict[str, Any] = {
    "name": "update_profile",
    "description": "Return the incrementally refined client profile.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "pain_points": {"type": "array", "items": {"type": "string"}},
            "objections": {"type": "array", "items": {"type": "string"}},
            "won_arguments": {"type": "array", "items": {"type": "string"}},
            "personal_notes": {"type": "string"},
        },
        "required": [
            "summary",
            "pain_points",
            "objections",
            "won_arguments",
            "personal_notes",
        ],
    },
}


def _format_current_profile(profile: ClientProfile | None) -> str:
    if profile is None:
        return "(профиль пока пустой)"
    payload = {
        "summary": profile.summary,
        "pain_points": profile.pain_points,
        "objections": profile.objections,
        "won_arguments": profile.won_arguments,
        "personal_notes": profile.personal_notes,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _format_history(messages: list[MessageModel]) -> str:
    if not messages:
        return "(переписки нет)"
    lines = []
    for m in messages:
        ts = (m.sent_at or m.created_at).strftime("%Y-%m-%d %H:%M")
        tag = "IN" if m.direction == MessageDirection.IN else "OUT"
        body = (m.text or "").strip() or "(без текста)"
        lines.append(f"[{tag} {ts}] {body}")
    return "\n".join(lines)


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


async def _load_history(session: AsyncSession, client_id: Any) -> list[MessageModel]:
    rows = await session.execute(
        select(MessageModel)
        .join(Conversation, Conversation.id == MessageModel.conversation_id)
        .where(Conversation.client_id == client_id)
        .order_by(MessageModel.sent_at.desc().nulls_last(), MessageModel.created_at.desc())
        .limit(_MAX_HISTORY)
    )
    return list(reversed(rows.scalars().all()))


async def update_profile(session: AsyncSession, client: Client) -> ClientProfile | None:
    """Run the profiler on a client and persist the refined profile."""
    profile = await session.scalar(
        select(ClientProfile)
        .where(ClientProfile.client_id == client.id)
        .options(selectinload(ClientProfile.client))
    )
    history = await _load_history(session, client.id)
    if not history:
        logger.debug("Skipping profile update for {} — no history", client.slug)
        return profile

    user_content = (
        f"Клиент: {client.name or client.slug} "
        f"(@{client.telegram_username or '—'})\n"
        f"Тип бизнеса: {client.business_type.value}, стадия: {client.stage.value}, "
        f"температура: {client.temperature.value}\n\n"
        "Текущий профиль:\n"
        f"{_format_current_profile(profile)}\n\n"
        "Переписка (по времени):\n"
        f"{_format_history(history)}"
    )

    response = await ai_client.call(
        request_type="update_profile",
        model=settings.claude_sonnet_model,
        system=[
            {
                "type": "text",
                "text": PROFILER_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        tools=[_PROFILER_TOOL],
        tool_choice={"type": "tool", "name": "update_profile"},
        max_tokens=2048,
        temperature=0.2,
    )

    raw = extract_tool_input(response, "update_profile")
    if raw is None:
        logger.warning("Profiler did not call update_profile tool for {}", client.slug)
        return profile

    summary = str(raw.get("summary") or "").strip()
    pain_points = _as_str_list(raw.get("pain_points"))
    objections = _as_str_list(raw.get("objections"))
    won_arguments = _as_str_list(raw.get("won_arguments"))
    personal_notes = str(raw.get("personal_notes") or "").strip()

    if profile is None:
        profile = ClientProfile(
            client_id=client.id,
            summary=summary or None,
            pain_points=pain_points,
            objections=objections,
            won_arguments=won_arguments,
            personal_notes=personal_notes or None,
        )
        session.add(profile)
    else:
        profile.summary = summary or None
        profile.pain_points = pain_points
        profile.objections = objections
        profile.won_arguments = won_arguments
        profile.personal_notes = personal_notes or None

    await session.commit()
    logger.info("Updated profile for client {}", client.slug)
    return profile
