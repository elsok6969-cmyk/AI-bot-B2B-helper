"""Tests for the Haiku analyzer."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.ai.analyzer import analyze_inbound
from src.db.models import Message as MessageModel
from src.db.models import MessageDirection, MessageSource


def _fake_anthropic_message(content_blocks, *, model="claude-haiku-4-5", in_t=100, out_t=20):
    usage = SimpleNamespace(
        input_tokens=in_t,
        output_tokens=out_t,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return SimpleNamespace(content=content_blocks, usage=usage, model=model, stop_reason="end_turn")


def _tool_use_response(tool_input: dict):
    return _fake_anthropic_message(
        [SimpleNamespace(type="tool_use", name="report_analysis", input=tool_input, id="tu")]
    )


def _make_message(text: str = "когда отгрузка?") -> MessageModel:
    return MessageModel(
        conversation_id=None,  # not persisted; analyzer doesn't use it
        direction=MessageDirection.IN,
        source=MessageSource.TG_BUSINESS,
        text=text,
        raw_payload={},
        sent_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_analyze_inbound_parses_tool_call():
    fake_input = {
        "intent": "price_request",
        "sentiment": "positive",
        "urgency": "normal",
        "suggested_action": "Отправить прайс с прогрессивной шкалой.",
    }
    msg = _make_message("Подскажите цены")

    async def fake_create(**kwargs):
        assert kwargs["model"] == "claude-haiku-4-5"
        tools = kwargs["tools"]
        assert any(t["name"] == "report_analysis" for t in tools)
        assert kwargs["tool_choice"] == {"type": "tool", "name": "report_analysis"}
        # The analyzer feeds context in the user content
        assert "Подскажите цены" in kwargs["messages"][0]["content"]
        return _tool_use_response(fake_input)

    with patch("src.ai.client.ai_client._client.messages.create", new=fake_create):
        result = await analyze_inbound(message=msg, context=[])

    assert result["intent"] == "price_request"
    assert result["sentiment"] == "positive"
    assert result["urgency"] == "normal"
    assert "прайс" in result["suggested_action"]


@pytest.mark.asyncio
async def test_analyze_inbound_normalizes_invalid_enum_values():
    """Unknown intent / sentiment / urgency strings fall back to defaults."""
    fake_input = {
        "intent": "weather_chat",  # not in INTENT_VALUES
        "sentiment": "mixed",  # not in SENTIMENT_VALUES
        "urgency": "URGENT",  # wrong case
        "suggested_action": "test",
    }

    async def fake_create(**_):
        return _tool_use_response(fake_input)

    with patch("src.ai.client.ai_client._client.messages.create", new=fake_create):
        result = await analyze_inbound(message=_make_message(), context=[])

    assert result["intent"] == "other"
    assert result["sentiment"] == "neutral"
    assert result["urgency"] == "normal"
    assert result["suggested_action"] == "test"


@pytest.mark.asyncio
async def test_analyze_inbound_handles_no_tool_call():
    """If the model returns plain text instead of calling the tool, return defaults."""
    plain = _fake_anthropic_message([SimpleNamespace(type="text", text="I refuse to use the tool")])

    async def fake_create(**_):
        return plain

    with patch("src.ai.client.ai_client._client.messages.create", new=fake_create):
        result = await analyze_inbound(message=_make_message(), context=[])

    assert result["intent"] == "other"
    assert result["sentiment"] == "neutral"
    assert result["urgency"] == "normal"
    assert result["suggested_action"] == ""
