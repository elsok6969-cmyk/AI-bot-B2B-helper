from __future__ import annotations

import secrets
from typing import Any

from aiogram import Bot
from aiogram.types import ErrorEvent, Update

from src.utils.logger import logger


def _manager_chat_id(update: Update | None) -> int | None:
    """Pick the manager-facing chat from an update.

    We only message back for direct messages and callback queries; business_*
    updates would route the reply back to a client's chat, which is the
    wrong destination for "something broke".
    """
    if update is None:
        return None
    msg = update.message
    if msg is not None and msg.business_connection_id is None and msg.chat is not None:
        return msg.chat.id
    cb = update.callback_query
    if cb is not None and cb.message is not None and cb.message.chat is not None:
        return cb.message.chat.id
    return None


async def on_error(event: ErrorEvent, bot: Bot, **_: Any) -> bool:
    log_id = secrets.token_hex(4)
    update_id = event.update.update_id if event.update is not None else "?"
    logger.opt(exception=event.exception).error(
        "[err:{}] handler error update_id={}", log_id, update_id
    )

    chat_id = _manager_chat_id(event.update)
    if chat_id is not None:
        try:
            await bot.send_message(
                chat_id,
                f"⚠️ Что-то сломалось. Лог: <code>{log_id}</code>",
            )
        except Exception:
            logger.exception("Failed to notify manager about error {}", log_id)

    return True
