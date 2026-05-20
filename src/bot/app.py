from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.errors import on_error
from src.bot.handlers import (
    business_connection,
    business_messages,
    commands,
    manual_entry,
)
from src.bot.middleware import AuthMiddleware, DbSessionMiddleware
from src.config import settings


def create_bot() -> Bot:
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    # Every update gets its own async DB session.
    dp.update.outer_middleware(DbSessionMiddleware())

    # AuthMiddleware applies to direct messages and callback queries from
    # managers (commands + FSM steps). Business updates go through a
    # different observer (dp.business_*) and resolve the manager via the
    # BusinessConnection row instead.
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())

    dp.include_router(commands.router)
    dp.include_router(manual_entry.router)
    dp.include_router(business_connection.router)
    dp.include_router(business_messages.router)

    dp.errors.register(on_error)

    return dp
