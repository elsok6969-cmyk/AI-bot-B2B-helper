"""Process-local bot reference for scheduler jobs.

APScheduler with SQLAlchemyJobStore pickles job arguments — a `Bot`
instance is not picklable. Jobs read the bot from this module instead,
and main.py sets it on startup.
"""

from __future__ import annotations

from aiogram import Bot

_bot: Bot | None = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def get_bot() -> Bot:
    if _bot is None:
        raise RuntimeError("Scheduler bot context is not initialized")
    return _bot
