from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.config import settings as app_settings
from src.services.crypto import is_configured
from src.web.deps import templates

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "settings": {
                "ai_provider": app_settings.ai_provider,
                "drafts_autogenerate": app_settings.drafts_autogenerate,
                "mail_enabled": app_settings.mail_enabled,
                "mail_poll_interval_minutes": app_settings.mail_poll_interval_minutes,
                "telethon_enabled": app_settings.telethon_enabled,
                "secrets_ready": is_configured(),
                "telethon_api_ready": bool(
                    app_settings.telethon_api_id
                    and app_settings.telethon_api_hash.get_secret_value()
                ),
            }
        },
    )
