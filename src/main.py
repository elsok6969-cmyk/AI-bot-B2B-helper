import asyncio
import sys

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.bot.app import create_bot, create_dispatcher
from src.config import settings
from src.db.seed import ensure_owner
from src.health import start_health_server
from src.scheduler.context import set_bot
from src.scheduler.jobs import (
    check_reminders_job,
    daily_digest_job,
    nightly_profile_refresh,
)
from src.utils.logger import logger, setup_logger


def _sync_database_url() -> str:
    """APScheduler's SQLAlchemyJobStore is sync — swap the async driver."""
    return settings.database_url.replace("+asyncpg", "+psycopg")


async def run() -> None:
    setup_logger(settings.log_level)

    bot = create_bot()
    dp = create_dispatcher()
    set_bot(bot)

    scheduler = AsyncIOScheduler(
        timezone=settings.tz,
        jobstores={"default": SQLAlchemyJobStore(url=_sync_database_url())},
    )

    health_runner = await start_health_server()

    @dp.startup()
    async def _on_startup() -> None:
        await ensure_owner()

        scheduler.add_job(
            daily_digest_job,
            trigger=CronTrigger(hour=settings.daily_digest_hour, minute=0),
            id="daily_digest",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            check_reminders_job,
            trigger=IntervalTrigger(minutes=settings.reminder_check_interval_minutes),
            id="check_reminders",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            nightly_profile_refresh,
            trigger=CronTrigger(hour=settings.profile_update_hour, minute=0),
            id="nightly_profile_refresh",
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

    # Временно получаем ВСЕ updates без фильтрации, чтобы проверить,
    # приходят ли business-сообщения вообще.
    try:
        await dp.start_polling(bot)
    finally:
        try:
            await health_runner.cleanup()
        except Exception:
            logger.exception("Failed to clean up health server")


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
