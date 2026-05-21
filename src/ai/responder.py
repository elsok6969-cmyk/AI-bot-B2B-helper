from __future__ import annotations

import json
from typing import Any

from src.ai.client import ai_client, extract_tool_input
from src.ai.prompts import RESPONDER_SYSTEM_PROMPT
from src.config import settings
from src.db.models import Client, ClientProfile, MessageDirection
from src.db.models import Message as MessageModel
from src.utils.logger import logger

VARIANT_LABELS = ("formal", "friendly", "closing")

_RESPONDER_TOOL: dict[str, Any] = {
    "name": "report_replies",
    "description": "Report 2-3 reply variants in different tonalities.",
    "input_schema": {
        "type": "object",
        "properties": {
            "variants": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "enum": list(VARIANT_LABELS)},
                        "text": {"type": "string"},
                    },
                    "required": ["label", "text"],
                },
            }
        },
        "required": ["variants"],
    },
}


def _format_history(messages: list[MessageModel]) -> str:
    if not messages:
        return "(история пуста)"
    lines = []
    for m in messages:
        ts = (m.sent_at or m.created_at).strftime("%Y-%m-%d %H:%M")
        tag = "IN" if m.direction == MessageDirection.IN else "OUT"
        body = (m.text or "").strip() or "(без текста)"
        lines.append(f"[{tag} {ts}] {body}")
    return "\n".join(lines)


def _format_profile(client: Client, profile: ClientProfile | None) -> str:
    payload: dict[str, Any] = {
        "name": client.name,
        "telegram_username": client.telegram_username,
        "business_type": client.business_type.value,
        "stage": client.stage.value,
        "temperature": client.temperature.value,
        "est_volume": client.est_volume,
        "interest_categories": client.interest_categories,
    }
    if profile is not None:
        payload.update(
            {
                "summary": profile.summary,
                "pain_points": profile.pain_points,
                "objections": profile.objections,
                "won_arguments": profile.won_arguments,
                "personal_notes": profile.personal_notes,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def suggest_reply(
    *,
    client: Client,
    profile: ClientProfile | None,
    history: list[MessageModel],
) -> list[dict[str, str]]:
    """Generate 2-3 reply variants for the latest inbound message.

    `history` should be the last ~30 messages in chronological order.
    Returns a list of {label, text} dicts. Never auto-sends.
    """
    user_content = (
        "Карточка клиента:\n"
        f"{_format_profile(client, profile)}\n\n"
        "Переписка (последние сообщения, по времени):\n"
        f"{_format_history(history)}\n\n"
        "Сгенерируй варианты ответа на ПОСЛЕДНЕЕ входящее сообщение клиента."
    )

    response = await ai_client.call(
        request_type="suggest_reply",
        model=settings.smart_model,
        system=[
            {
                "type": "text",
                "text": RESPONDER_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        tools=[_RESPONDER_TOOL],
        tool_choice={"type": "tool", "name": "report_replies"},
        max_tokens=2048,
        temperature=0.5,
    )

    raw = extract_tool_input(response, "report_replies")
    if raw is None:
        logger.warning("Responder did not call report_replies tool")
        return []

    variants_raw = raw.get("variants") or []
    out: list[dict[str, str]] = []
    seen_labels: set[str] = set()
    for v in variants_raw:
        if not isinstance(v, dict):
            continue
        label = str(v.get("label", "")).lower()
        text = str(v.get("text", "")).strip()
        if not text:
            continue
        if label not in VARIANT_LABELS:
            label = "formal"
        if label in seen_labels:
            continue
        seen_labels.add(label)
        out.append({"label": label, "text": text})
    return out
