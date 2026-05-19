import asyncio
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.bot.app import create_bot, create_dispatcher
from src.config import settings
from src.db.seed import ensure_owner
from src.scheduler.jobs import daily_profile_update
from src.utils.logger import logger, setup_logger


async def run() -> None:
    setup_logger(settings.log_level)

    bot = create_bot()
    dp = create_dispatcher()
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    @dp.startup()
    async def _on_startup() -> None:
        await ensure_owner()
        scheduler.add_job(
            daily_profile_update,
            trigger=CronTrigger(hour=settings.profile_update_hour, minute=0),
            id="daily_profile_update",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
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
