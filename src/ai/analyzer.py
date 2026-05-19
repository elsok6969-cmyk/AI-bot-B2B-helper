from __future__ import annotations

from typing import Any

from src.ai.client import ai_client, extract_tool_input
from src.ai.prompts import ANALYZER_SYSTEM_PROMPT
from src.config import settings
from src.db.models import Message as MessageModel
from src.db.models import MessageDirection
from src.utils.logger import logger

INTENT_VALUES = (
    "price_request",
    "objection",
    "smalltalk",
    "order",
    "complaint",
    "info_request",
    "other",
)
SENTIMENT_VALUES = ("positive", "neutral", "negative")
URGENCY_VALUES = ("low", "normal", "high")

_ANALYZER_TOOL: dict[str, Any] = {
    "name": "report_analysis",
    "description": "Report the structured analysis of the inbound message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": list(INTENT_VALUES)},
            "sentiment": {"type": "string", "enum": list(SENTIMENT_VALUES)},
            "urgency": {"type": "string", "enum": list(URGENCY_VALUES)},
            "suggested_action": {"type": "string"},
        },
        "required": ["intent", "sentiment", "urgency", "suggested_action"],
    },
}


def _format_context(history: list[MessageModel]) -> str:
    if not history:
        return "(контекста нет)"
    lines = []
    for m in history:
        ts = (m.sent_at or m.created_at).strftime("%Y-%m-%d %H:%M")
        tag = "IN" if m.direction == MessageDirection.IN else "OUT"
        body = (m.text or "").strip() or "(без текста)"
        lines.append(f"[{tag} {ts}] {body}")
    return "\n".join(lines)


def _normalize(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    return fallback


async def analyze_inbound(
    *,
    message: MessageModel,
    context: list[MessageModel],
) -> dict[str, Any]:
    """Run Haiku-based classification on a single inbound message.

    Returns a dict with intent / sentiment / urgency / suggested_action.
    Falls back to safe defaults if the model declines to call the tool.
    """
    user_content = (
        "Контекст переписки (последние сообщения, по времени):\n"
        f"{_format_context(context)}\n\n"
        "Последнее входящее сообщение для анализа:\n"
        f'"{(message.text or "").strip()}"'
    )

    response = await ai_client.call(
        request_type="analyze_inbound",
        model=settings.claude_haiku_model,
        system=[
            {
                "type": "text",
                "text": ANALYZER_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        tools=[_ANALYZER_TOOL],
        tool_choice={"type": "tool", "name": "report_analysis"},
        max_tokens=512,
        temperature=0.0,
    )

    raw = extract_tool_input(response, "report_analysis")
    if raw is None:
        logger.warning("Analyzer did not call report_analysis tool; using defaults")
        return {
            "intent": "other",
            "sentiment": "neutral",
            "urgency": "normal",
            "suggested_action": "",
            "model": response.model,
        }

    return {
        "intent": _normalize(raw.get("intent"), INTENT_VALUES, "other"),
        "sentiment": _normalize(raw.get("sentiment"), SENTIMENT_VALUES, "neutral"),
        "urgency": _normalize(raw.get("urgency"), URGENCY_VALUES, "normal"),
        "suggested_action": str(raw.get("suggested_action") or "").strip(),
        "model": response.model,
    }
