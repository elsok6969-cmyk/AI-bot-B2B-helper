from __future__ import annotations

from aiohttp import web
from sqlalchemy import text

from src.db.session import engine
from src.utils.logger import logger


async def _health(_: web.Request) -> web.Response:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Healthcheck DB ping failed: {}", exc)
        return web.json_response({"status": "down", "db": "fail"}, status=503)
    return web.json_response({"status": "ok"})


async def start_health_server(host: str = "127.0.0.1", port: int = 8080) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Health server listening on {}:{}", host, port)
    return runner
