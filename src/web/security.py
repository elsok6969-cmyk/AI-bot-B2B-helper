"""Web security middleware: optional bearer token + Host header guard.

We have no real auth (this is a single-user local tool), but we defend
against three concrete threats:

1. **LAN exposure** — if the user sets `WEB_HOST=0.0.0.0`, we require
   them to also set `WEB_ACCESS_TOKEN` and then enforce it on every
   request. Without the token, all requests get 401.

2. **DNS rebinding** — a malicious page can resolve `attacker.com` to
   `127.0.0.1` and POST to it via the browser. We reject any request
   whose `Host:` header is not loopback / explicitly allowed.

3. **CSRF on state-changing routes** — same-origin-only POSTs by
   checking the `Origin` header against the resolved `Host`. Loose
   compared to a per-form CSRF token, but matches the threat model
   (single-user local app, browsers send `Origin` on all cross-origin
   POSTs).
"""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from src.config import settings

COOKIE_NAME = "mynota_token"
HEADER_NAME = "x-mynota-token"
QUERY_PARAM = "token"

_DEFAULT_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def _expected_token() -> str:
    return settings.web_access_token.get_secret_value().strip()


def _host_allowed(host_header: str) -> bool:
    if not host_header:
        return False
    hostname = host_header.split(":", 1)[0].strip().lower()
    if hostname in _DEFAULT_ALLOWED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        # Hostnames that aren't IPs are rejected — DNS rebinding defence.
        return False
    return ip.is_loopback


def _origin_matches(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        # No Origin: same-origin GET or non-browser client. Allow.
        return True
    try:
        origin_host = urlsplit(origin).hostname or ""
    except ValueError:
        return False
    expected_host = request.headers.get("host", "").split(":", 1)[0]
    return origin_host.lower() == expected_host.lower()


def _token_provided(request: Request) -> str | None:
    header = request.headers.get(HEADER_NAME)
    if header:
        return header.strip()
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie.strip()
    return request.query_params.get(QUERY_PARAM)


def _login_page(error: str | None = None) -> HTMLResponse:
    err_html = (
        f'<div style="color:#b91c1c;margin-bottom:1rem">{error}</div>' if error else ""
    )
    body = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Mynota — вход</title>
<style>body{{font-family:system-ui;background:#f8fafc;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}}
.box{{background:white;padding:2rem;border-radius:.5rem;
border:1px solid #e2e8f0;max-width:24rem;width:100%}}
input,button{{font-size:1rem;padding:.5rem;border-radius:.25rem;
border:1px solid #cbd5e1;width:100%;box-sizing:border-box}}
button{{margin-top:.75rem;background:#2563eb;color:white;border:none;cursor:pointer}}
h1{{margin-top:0;font-size:1.25rem}}</style></head>
<body><form class="box" method="post" action="/auth/login">
<h1>Mynota — доступ</h1>{err_html}
<label style="font-size:.85rem;color:#475569">Токен (из <code>WEB_ACCESS_TOKEN</code>)</label>
<input type="password" name="token" autofocus autocomplete="current-password" required>
<button type="submit">Войти</button>
</form></body></html>"""
    return HTMLResponse(body, status_code=401)


async def security_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # 1. Host header guard — always on. Blocks DNS rebinding to 127.0.0.1.
    if not _host_allowed(request.headers.get("host", "")):
        return JSONResponse({"detail": "Host not allowed"}, status_code=400)

    path = request.url.path
    # /static and the login endpoint itself are exempt.
    if path.startswith("/static/") or path.startswith("/auth/"):
        return await call_next(request)

    expected = _expected_token()
    if expected:
        provided = _token_provided(request)
        if not provided or not hmac.compare_digest(provided, expected):
            if request.method == "GET":
                return _login_page()
            return JSONResponse({"detail": "Auth required"}, status_code=401)

    # 2. Same-origin enforcement for state-changing methods.
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_matches(request):
        return JSONResponse({"detail": "Cross-origin denied"}, status_code=403)

    response = await call_next(request)

    # 3. Clear one-shot flash cookie on any GET that rendered something.
    #    The user has now seen the banner (in the template); don't show it again.
    from src.web.flash import COOKIE as _FLASH_COOKIE

    if (
        request.method == "GET"
        and _FLASH_COOKIE in request.cookies
        and not response.headers.get("set-cookie", "").lower().count(_FLASH_COOKIE.lower())
    ):
        response.delete_cookie(_FLASH_COOKIE)

    return response
