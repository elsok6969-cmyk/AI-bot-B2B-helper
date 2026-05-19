from __future__ import annotations

from decimal import Decimal
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage

from src.config import settings
from src.db.models import AILog
from src.db.session import SessionLocal
from src.utils.logger import logger

# USD per 1,000,000 tokens (input, output) — kept here so we can log a
# real cost into ai_logs without a separate pricing service.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
}

_DEFAULT_PRICING = (3.0, 15.0)


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    pricing = MODEL_PRICING_USD_PER_MTOK.get(model)
    if pricing is None:
        logger.warning("No pricing entry for model {} — falling back to Sonnet pricing", model)
        pricing = _DEFAULT_PRICING
    input_rate, output_rate = pricing
    cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return Decimal(str(cost)).quantize(Decimal("0.000001"))


class AIClient:
    def __init__(self) -> None:
        # max_retries=3 enables the SDK's built-in exponential backoff for
        # 429 / 5xx; timeout=60s keeps a single call from hanging the bot loop.
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_retries=3,
            timeout=60.0,
        )

    async def call(
        self,
        *,
        request_type: str,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> AnthropicMessage:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = await self._client.messages.create(**kwargs)
        await self._log_usage(request_type, response)
        return response

    @staticmethod
    async def _log_usage(request_type: str, response: AnthropicMessage) -> None:
        usage = response.usage
        input_tokens = (
            usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0)
        )
        output_tokens = usage.output_tokens
        cost = _compute_cost(response.model, input_tokens, output_tokens)

        async with SessionLocal() as session:
            session.add(
                AILog(
                    request_type=request_type,
                    model=response.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                )
            )
            await session.commit()

        logger.info(
            "AI {} ({}): in={} out={} cost=${}",
            request_type,
            response.model,
            input_tokens,
            output_tokens,
            cost,
        )


def extract_tool_input(response: AnthropicMessage, tool_name: str) -> dict[str, Any] | None:
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return dict(block.input) if not isinstance(block.input, dict) else block.input
    return None


ai_client = AIClient()
