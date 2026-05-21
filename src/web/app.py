from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.utils.logger import logger
from src.web.routes import clients, dashboard, drafts, mail, telethon
from src.web.routes import settings as settings_route

_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Mynota dashboard", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(dashboard.router)
    app.include_router(clients.router)
    app.include_router(drafts.router)
    app.include_router(mail.router)
    app.include_router(telethon.router)
    app.include_router(settings_route.router)
    return app


async def start_web_server() -> uvicorn.Server:
    """Start uvicorn in the same event loop and return a handle for shutdown."""
    config = uvicorn.Config(
        app=create_app(),
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level.lower(),
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    logger.info(
        "Web UI listening on http://{}:{}", settings.web_host, settings.web_port
    )
    return server
