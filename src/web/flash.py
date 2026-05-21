"""One-shot flash messages via a short-lived cookie.

We don't have server sessions (no auth, single user) so we stash the
message in a cookie that the next page consumes and clears. Good
enough for "успешно отправлено" / "ошибка: ..." banners after POST/redirect.
"""

from __future__ import annotations

import base64
import json

from fastapi import Request
from fastapi.responses import Response

COOKIE = "mynota_flash"


def attach_flash_to_redirect(response: Response, message: str, *, level: str = "info") -> None:
    """Mutates `response` to carry a flash. Use after creating a RedirectResponse."""
    payload = {"m": message[:500], "l": level}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    response.set_cookie(
        COOKIE,
        encoded,
        max_age=60,
        httponly=True,
        samesite="strict",
        secure=False,
    )


def take_flash(request: Request) -> dict[str, str] | None:
    """Read-and-clear. Call from templates via `{{ take_flash(request) }}`."""
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        decoded = base64.urlsafe_b64decode(raw.encode()).decode()
        data = json.loads(decoded)
        msg = str(data.get("m", "")).strip()
        if not msg:
            return None
        return {"message": msg, "level": data.get("l", "info")}
    except Exception:
        return None
