"""Web security middleware: host guard, origin guard, token check."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.web.security import _host_allowed, _origin_matches


def _req(headers: dict[str, str]) -> MagicMock:
    req = MagicMock()
    # Starlette Request.headers is a Headers object; here a dict-like is fine.
    req.headers = {k.lower(): v for k, v in headers.items()}
    return req


def test_host_allowed_loopback() -> None:
    assert _host_allowed("localhost") is True
    assert _host_allowed("localhost:8090") is True
    assert _host_allowed("127.0.0.1") is True
    assert _host_allowed("127.0.0.1:8090") is True
    assert _host_allowed("::1") is True
    assert _host_allowed("[::1]:8090") is True


def test_host_rejected_external() -> None:
    assert _host_allowed("evil.com") is False
    assert _host_allowed("attacker.example") is False
    assert _host_allowed("8.8.8.8") is False
    assert _host_allowed("") is False


def test_origin_matches_same_host() -> None:
    req = _req({"host": "localhost:8090", "origin": "http://localhost:8090"})
    assert _origin_matches(req) is True


def test_origin_mismatch_blocks() -> None:
    req = _req({"host": "localhost:8090", "origin": "http://evil.com"})
    assert _origin_matches(req) is False


def test_origin_missing_allows() -> None:
    # No Origin/Referer = same-origin GET or non-browser; we don't block.
    req = _req({"host": "localhost:8090"})
    assert _origin_matches(req) is True


def test_origin_falls_back_to_referer() -> None:
    req = _req({"host": "localhost:8090", "referer": "http://localhost:8090/foo"})
    assert _origin_matches(req) is True
    req2 = _req({"host": "localhost:8090", "referer": "http://evil.com/foo"})
    assert _origin_matches(req2) is False
