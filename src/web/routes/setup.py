"""First-run setup page: edit core .env values from the browser.

We don't ship a full settings UI — only the handful of bootstrap values
that must exist *before* the bot/web can do anything useful:

- BOT_TOKEN (from @BotFather)
- OWNER_TELEGRAM_ID (your TG user id)
- AI_PROVIDER + key (anthropic / kimi)
- TELETHON_API_ID / TELETHON_API_HASH (optional, for Telethon ingest)

After saving, the user is told to restart the launcher; we can't
hot-reload pydantic-settings, the bot instance, or the Telethon worker
inside a running process safely.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.config import settings
from src.web.deps import templates
from src.web.flash import attach_flash_to_redirect

router = APIRouter(prefix="/setup", tags=["setup"])

# .env sits at the project root. Computed from the location of this module:
# src/web/routes/setup.py → parents[3] = repo root.
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


# Keys we let the user edit. Other settings (DATABASE_URL, ports, etc.)
# the launcher sets — we don't expose them to avoid foot-guns.
_EDITABLE = (
    "BOT_TOKEN",
    "OWNER_TELEGRAM_ID",
    "AI_PROVIDER",
    "ANTHROPIC_API_KEY",
    "KIMI_API_KEY",
    "TELETHON_API_ID",
    "TELETHON_API_HASH",
)


def _read_env() -> dict[str, str]:
    """Parse .env into a dict. Missing file = empty dict."""
    if not ENV_FILE.exists():
        return {}
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _write_env_overlay(overlay: dict[str, str]) -> None:
    """Patch .env in place: replace any line matching a key in overlay,
    append the rest. Preserves comments and other unrelated keys."""
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.partition("=")[0].strip()
            if key in overlay:
                out.append(f"{key}={overlay[key]}")
                seen.add(key)
                continue
        out.append(raw)
    for key, val in overlay.items():
        if key not in seen:
            out.append(f"{key}={val}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


@router.get("", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    current = _read_env()
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "env": current,
            "env_path": str(ENV_FILE),
            "running_with": {
                "bot_token_set": bool(settings.bot_token.get_secret_value().strip()),
                "owner_id_set": bool(settings.owner_telegram_id),
                "ai_provider": settings.ai_provider,
                "telethon_api_id_set": bool(settings.telethon_api_id),
            },
        },
    )


@router.post("/save")
async def save_setup(
    bot_token: str = Form(default=""),
    owner_telegram_id: str = Form(default=""),
    ai_provider: str = Form(default="anthropic"),
    anthropic_api_key: str = Form(default=""),
    kimi_api_key: str = Form(default=""),
    telethon_api_id: str = Form(default=""),
    telethon_api_hash: str = Form(default=""),
) -> RedirectResponse:
    current = _read_env()
    overlay: dict[str, str] = {}

    # For each editable field, empty string = "keep current value", so
    # the user can edit one secret without re-pasting all of them.
    def _set(key: str, value: str) -> None:
        value = value.strip()
        if value:
            overlay[key] = value
        elif key not in current:
            overlay[key] = ""

    _set("BOT_TOKEN", bot_token)
    if owner_telegram_id.strip():
        if not owner_telegram_id.strip().lstrip("-").isdigit():
            resp = RedirectResponse(url="/setup", status_code=303)
            attach_flash_to_redirect(
                resp,
                "OWNER_TELEGRAM_ID должен быть числом (узнай через @userinfobot).",
                level="error",
            )
            return resp
        overlay["OWNER_TELEGRAM_ID"] = owner_telegram_id.strip()
    ai_provider = (ai_provider or "anthropic").strip().lower()
    if ai_provider not in ("anthropic", "kimi"):
        resp = RedirectResponse(url="/setup", status_code=303)
        attach_flash_to_redirect(resp, "AI_PROVIDER может быть anthropic или kimi.", level="error")
        return resp
    overlay["AI_PROVIDER"] = ai_provider
    _set("ANTHROPIC_API_KEY", anthropic_api_key)
    _set("KIMI_API_KEY", kimi_api_key)
    if telethon_api_id.strip():
        if not telethon_api_id.strip().isdigit():
            resp = RedirectResponse(url="/setup", status_code=303)
            attach_flash_to_redirect(
                resp,
                "TELETHON_API_ID должен быть числом (с https://my.telegram.org).",
                level="error",
            )
            return resp
        overlay["TELETHON_API_ID"] = telethon_api_id.strip()
    _set("TELETHON_API_HASH", telethon_api_hash)

    _write_env_overlay(overlay)

    resp = RedirectResponse(url="/setup", status_code=303)
    attach_flash_to_redirect(
        resp,
        "Сохранено в .env. Перезапусти лаунчер (Ctrl+C в терминале, потом "
        "снова двойной клик по Start.command) — изменения подхватятся.",
        level="success",
    )
    return resp
