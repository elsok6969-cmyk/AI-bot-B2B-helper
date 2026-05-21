from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from anthropic import AsyncAnthropic

from src.config import settings
from src.db.models import AILog
from src.db.session import SessionLocal
from src.utils.logger import logger

# USD per 1,000,000 tokens (input, output) — kept here so we can log a real
# cost into ai_logs without a separate pricing service. Unknown models fall
# back to $0 (with a warning) so a new provider doesn't crash on logging.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    # Kimi (Moonshot AI) — rough public list prices, May 2026
    "moonshot-v1-8k": (0.27, 0.27),
    "moonshot-v1-32k": (1.09, 1.09),
    "moonshot-v1-128k": (7.26, 7.26),
    "moonshot-v1-8k-vision-preview": (0.27, 0.27),
    "moonshot-v1-32k-vision-preview": (1.09, 1.09),
    "moonshot-v1-128k-vision-preview": (7.26, 7.26),
    "kimi-k2-0905-preview": (0.60, 2.50),
    "kimi-latest": (0.27, 0.27),
}


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    pricing = MODEL_PRICING_USD_PER_MTOK.get(model)
    if pricing is None:
        logger.warning("No pricing entry for model {} — logging cost as $0", model)
        pricing = (0.0, 0.0)
    input_rate, output_rate = pricing
    cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return Decimal(str(cost)).quantize(Decimal("0.000001"))


# ---------------------------------------------------------------------------
# Anthropic ↔ OpenAI shape conversion
# ---------------------------------------------------------------------------
#
# All callers (analyzer.py, responder.py, profiler.py, ocr.py) build requests
# in Anthropic shape (separate `system`, `messages` with content blocks,
# Anthropic tool schemas) and read responses as Anthropic content blocks via
# `extract_tool_input`. When AI_PROVIDER=kimi the AIClient calls the
# OpenAI-compatible Moonshot endpoint instead — we translate the request to
# OpenAI shape and translate the response back so the rest of the codebase
# is provider-agnostic.


def _anthropic_to_openai_messages(
    system: str | list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        if isinstance(system, str):
            out.append({"role": "system", "content": system})
        else:
            text = "\n".join(b.get("text", "") for b in system if b.get("type") == "text")
            if text:
                out.append({"role": "system", "content": text})

    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        converted: list[dict[str, Any]] = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                converted.append({"type": "text", "text": block["text"]})
            elif btype == "image":
                src = block["source"]
                if src.get("type") == "base64":
                    data_url = f"data:{src['media_type']};base64,{src['data']}"
                    converted.append({"type": "image_url", "image_url": {"url": data_url}})
                elif src.get("type") == "url":
                    converted.append({"type": "image_url", "image_url": {"url": src["url"]}})
        out.append({"role": role, "content": converted})
    return out


def _anthropic_to_openai_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _anthropic_to_openai_tool_choice(
    tool_choice: dict[str, Any] | None,
) -> dict[str, Any] | str | None:
    if tool_choice is None:
        return None
    t = tool_choice.get("type")
    if t == "tool":
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    if t in {"auto", "any", "none"}:
        return "auto" if t == "auto" else ("required" if t == "any" else "none")
    return tool_choice


def _openai_to_anthropic_response(openai_response: Any) -> SimpleNamespace:
    """Wrap an OpenAI chat completion in an Anthropic-shape SimpleNamespace.

    Produces the subset of fields that `extract_tool_input` and `_log_usage`
    actually read: ``.content`` (a list of ``type=text|tool_use`` blocks),
    ``.usage``, ``.model``, ``.stop_reason``.
    """
    choice = openai_response.choices[0]
    msg = choice.message
    content_blocks: list[SimpleNamespace] = []

    if msg.content:
        content_blocks.append(SimpleNamespace(type="text", text=msg.content))

    for tool_call in getattr(msg, "tool_calls", None) or []:
        try:
            tool_input = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            tool_input = {}
        content_blocks.append(
            SimpleNamespace(
                type="tool_use",
                name=tool_call.function.name,
                input=tool_input,
                id=tool_call.id,
            )
        )

    u = openai_response.usage
    usage = SimpleNamespace(
        input_tokens=getattr(u, "prompt_tokens", 0) or 0,
        output_tokens=getattr(u, "completion_tokens", 0) or 0,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=getattr(
            getattr(u, "prompt_tokens_details", None), "cached_tokens", 0
        )
        or 0,
    )

    return SimpleNamespace(
        content=content_blocks,
        usage=usage,
        model=openai_response.model,
        stop_reason="end_turn",
    )


class AIClient:
    def __init__(self) -> None:
        # Lazy: don't actually construct the underlying SDK until the
        # first call. This lets the web (and /setup) come up even when
        # ANTHROPIC_API_KEY / KIMI_API_KEY is empty on first run.
        self.provider = settings.ai_provider.strip().lower()
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        # max_retries=3 enables built-in exponential backoff for 429 / 5xx;
        # timeout=60s keeps a single call from hanging the bot loop.
        if self.provider == "anthropic":
            key = settings.anthropic_api_key.get_secret_value()
            if not key:
                raise RuntimeError("AI_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty")
            self._client = AsyncAnthropic(api_key=key, max_retries=3, timeout=60.0)
        elif self.provider == "kimi":
            from openai import AsyncOpenAI

            key = settings.kimi_api_key.get_secret_value()
            if not key:
                raise RuntimeError("AI_PROVIDER=kimi but KIMI_API_KEY is empty")
            self._client = AsyncOpenAI(
                api_key=key,
                base_url=settings.kimi_base_url,
                max_retries=3,
                timeout=60.0,
            )
            logger.info("AI provider: kimi @ {}", settings.kimi_base_url)
        else:
            raise RuntimeError(
                f"Unknown AI_PROVIDER={settings.ai_provider!r}; expected 'anthropic' or 'kimi'."
            )
        return self._client

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
    ) -> Any:
        if self.provider == "anthropic":
            response = await self._call_anthropic(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
            )
        else:
            response = await self._call_kimi(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
            )
        await self._log_usage(request_type, response)
        return response

    async def _call_anthropic(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
        temperature: float | None,
    ) -> Any:
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
        return await self._ensure_client().messages.create(**kwargs)

    async def _call_kimi(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
        max_tokens: int,
        tools: list[dict[str, Any]] | None,
        tool_choice: dict[str, Any] | None,
        temperature: float | None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": _anthropic_to_openai_messages(system, messages),
            "max_tokens": max_tokens,
        }
        oa_tools = _anthropic_to_openai_tools(tools)
        if oa_tools is not None:
            kwargs["tools"] = oa_tools
        oa_choice = _anthropic_to_openai_tool_choice(tool_choice)
        if oa_choice is not None:
            kwargs["tool_choice"] = oa_choice
        if temperature is not None:
            kwargs["temperature"] = temperature

        raw = await self._ensure_client().chat.completions.create(**kwargs)
        return _openai_to_anthropic_response(raw)

    @staticmethod
    async def _log_usage(request_type: str, response: Any) -> None:
        usage = response.usage
        input_tokens = (
            usage.input_tokens
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
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


def extract_tool_input(response: Any, tool_name: str) -> dict[str, Any] | None:
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return dict(block.input) if not isinstance(block.input, dict) else block.input
    return None


ai_client = AIClient()
