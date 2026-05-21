"""Flash-message cookie roundtrip."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.responses import Response

from src.web.flash import attach_flash_to_redirect, take_flash


def test_attach_and_take_roundtrip() -> None:
    resp = Response()
    attach_flash_to_redirect(resp, "ok done", level="success")
    # Pull the cookie value out of the set-cookie header.
    set_cookie = resp.headers["set-cookie"]
    assert "mynota_flash=" in set_cookie
    # Build a fake request with that cookie and confirm take returns it.
    raw_cookie = set_cookie.split("mynota_flash=", 1)[1].split(";", 1)[0]
    req = MagicMock()
    req.cookies = {"mynota_flash": raw_cookie}
    msg = take_flash(req)
    assert msg == {"message": "ok done", "level": "success"}


def test_take_flash_missing_returns_none() -> None:
    req = MagicMock()
    req.cookies = {}
    assert take_flash(req) is None


def test_take_flash_garbage_returns_none() -> None:
    req = MagicMock()
    req.cookies = {"mynota_flash": "not-base64-at-all"}
    assert take_flash(req) is None


def test_message_truncated() -> None:
    resp = Response()
    attach_flash_to_redirect(resp, "x" * 5000, level="info")
    raw = resp.headers["set-cookie"].split("mynota_flash=", 1)[1].split(";", 1)[0]
    req = MagicMock()
    req.cookies = {"mynota_flash": raw}
    msg = take_flash(req)
    assert msg is not None
    assert len(msg["message"]) <= 500
