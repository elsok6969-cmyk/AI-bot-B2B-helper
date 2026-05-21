from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import settings
from src.services.channels import ChannelSendError
from src.utils.logger import logger
from src.web.deps import NoOwnerError, render_welcome
from src.web.flash import attach_flash_to_redirect, take_flash
from src.web.routes import auth, clients, dashboard, drafts, mail, setup, telethon
from src.web.routes import settings as settings_route
from src.web.security import security_middleware

_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Mynota dashboard", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.add_middleware(BaseHTTPMiddleware, dispatch=security_middleware)
    app.include_router(auth.router)
    app.include_router(setup.router)
    app.include_router(dashboard.router)
    app.include_router(clients.router)
    app.include_router(drafts.router)
    app.include_router(mail.router)
    app.include_router(telethon.router)
    app.include_router(settings_route.router)

    @app.exception_handler(NoOwnerError)
    async def _no_owner(_: Request, __: NoOwnerError) -> Response:
        return render_welcome()

    @app.exception_handler(ChannelSendError)
    async def _send_error(request: Request, exc: ChannelSendError) -> Response:
        # Bounce back to wherever the user came from with a flash message.
        target = request.headers.get("referer") or "/"
        resp: Response = JSONResponse(
            {"detail": str(exc)}, status_code=400
        ) if request.headers.get("accept", "").startswith("application/json") else (
            HTMLResponse(
                f'<meta http-equiv="refresh" content="0;url={target}">',
                status_code=303,
                headers={"Location": target},
            )
        )
        attach_flash_to_redirect(resp, f"Ошибка отправки: {exc}", level="error")
        return resp

    # Make take_flash available in templates so the layout can render banners.
    from src.web.deps import templates

    templates.env.globals["take_flash"] = take_flash
    return app


async def start_web_server() -> uvicorn.Server:
    """Start uvicorn in the same event loop and return a handle for shutdown."""
    if (
        settings.web_host not in ("127.0.0.1", "localhost", "::1")
        and not settings.web_access_token.get_secret_value().strip()
    ):
        logger.warning(
            "WEB_HOST={} but WEB_ACCESS_TOKEN is empty — the dashboard "
            "is exposed without auth. Set WEB_ACCESS_TOKEN in .env.",
            settings.web_host,
        )

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
