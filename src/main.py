import asyncio
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.bot.app import create_bot, create_dispatcher
from src.config import settings
from src.db.seed import ensure_owner
from src.utils.logger import logger, setup_logger


async def run() -> None:
    setup_logger(settings.log_level)

    bot = create_bot()
    dp = create_dispatcher()
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    @dp.startup()
    async def _on_startup() -> None:
        await ensure_owner()
        scheduler.start()
        logger.info("Bot started")

    @dp.shutdown()
    async def _on_shutdown() -> None:
        logger.info("Shutting down...")
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("Bot stopped")

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
