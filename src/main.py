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
from src.integrations import telethon_runtime
from src.scheduler.context import set_bot
from src.scheduler.jobs import (
    check_reminders_job,
    daily_digest_job,
    mail_poll_job,
    nightly_profile_refresh,
)
from src.utils.logger import logger, setup_logger
from src.web.app import start_web_server


def _sync_database_url() -> str:
    """APScheduler's SQLAlchemyJobStore is sync — swap the async driver."""
    return settings.database_url.replace("+asyncpg", "+psycopg")


async def run() -> None:
    setup_logger(settings.log_level)

    bot = create_bot()
    dp = create_dispatcher()
    set_bot(bot)
    telethon_runtime.set_bot(bot)

    scheduler = AsyncIOScheduler(
        timezone=settings.tz,
        jobstores={"default": SQLAlchemyJobStore(url=_sync_database_url())},
    )

    await ensure_owner()

    health_runner = await start_health_server(
        host=settings.health_host, port=settings.health_port
    )

    web_server = None
    web_task: asyncio.Task[None] | None = None
    if settings.web_enabled:
        web_server = await start_web_server()
        web_task = asyncio.create_task(web_server.serve(), name="web_server")

    @dp.startup()
    async def _on_startup() -> None:
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
        if settings.mail_enabled:
            scheduler.add_job(
                mail_poll_job,
                trigger=IntervalTrigger(minutes=settings.mail_poll_interval_minutes),
                id="mail_poll",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

        scheduler.start()
        try:
            await telethon_runtime.start_worker()
        except Exception:
            logger.exception("Telethon worker failed to start")
        logger.info("Bot started")

    @dp.shutdown()
    async def _on_shutdown() -> None:
        logger.info("Shutting down...")
        if scheduler.running:
            scheduler.shutdown(wait=False)
        try:
            await telethon_runtime.shutdown_all()
        except Exception:
            logger.exception("Telethon shutdown failed")
        await bot.session.close()
        logger.info("Bot stopped")

    # Временно получаем ВСЕ updates без фильтрации, чтобы проверить,
    # приходят ли business-сообщения вообще.
    try:
        await dp.start_polling(bot)
    finally:
        if web_server is not None:
            web_server.should_exit = True
        if web_task is not None:
            try:
                await asyncio.wait_for(web_task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                web_task.cancel()
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
