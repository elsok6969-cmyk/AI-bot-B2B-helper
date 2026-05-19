import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import settings
from src.utils.logger import logger, setup_logger


async def run() -> None:
    setup_logger(settings.log_level)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    @dp.startup()
    async def _on_startup() -> None:
        scheduler.start()
        logger.info("Bot started")

    @dp.shutdown()
    async def _on_shutdown() -> None:
        logger.info("Shutting down...")
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("Bot stopped")

    await dp.start_polling(bot)


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
