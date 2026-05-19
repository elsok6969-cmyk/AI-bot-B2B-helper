from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.handlers import business_connection, business_messages, commands
from src.bot.middleware import AuthMiddleware, DbSessionMiddleware
from src.config import settings


def create_bot() -> Bot:
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    dp.update.outer_middleware(DbSessionMiddleware())

    commands.router.message.middleware(AuthMiddleware())

    dp.include_router(commands.router)
    dp.include_router(business_connection.router)
    dp.include_router(business_messages.router)

    return dp
