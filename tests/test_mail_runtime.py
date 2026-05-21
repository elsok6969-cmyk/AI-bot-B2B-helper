"""IMAP/SMTP helpers — pure parsing, no network."""

from __future__ import annotations

import email

import pytest

from src.integrations.mail_runtime import (
    MailError,
    _decode_mime_header,
    _extract_text,
    _parse_date,
    _parse_rfc822_size,
    _scrub,
    _strip_html,
    assert_safe_host,
    looks_like_email,
)


def test_extract_text_multipart_plain_wins() -> None:
    msg = email.message_from_bytes(
        b'Content-Type: multipart/alternative; boundary="b1"\n\n'
        b"--b1\n"
        b"Content-Type: text/plain; charset=utf-8\n"
        b"Content-Transfer-Encoding: 8bit\n\n"
        b"\xd0\xbf\xd0\xbb\xd0\xb5\xd0\xb9\xd0\xbd\n\n"
        b"--b1\n"
        b"Content-Type: text/html; charset=utf-8\n\n"
        b"<b>HTML fallback</b>\n"
        b"--b1--\n"
    )
    assert _extract_text(msg) == "плейн"


def test_extract_text_single_html() -> None:
    msg = email.message_from_bytes(
        b"Content-Type: text/html; charset=utf-8\n\n"
        b"<html><body><b>Hello</b> &amp; world<script>nasty</script></body></html>"
    )
    out = _extract_text(msg)
    assert "<" not in out and ">" not in out
    assert "nasty" not in out  # script body dropped
    assert "Hello" in out and "& world" in out


def test_extract_text_cp1251() -> None:
    msg = email.message_from_bytes(
        b"Content-Type: text/plain; charset=windows-1251\n"
        b"Content-Transfer-Encoding: 8bit\n\n"
        b"\xcf\xf0\xe8\xe2\xe5\xf2"
    )
    assert _extract_text(msg) == "Привет"


def test_scrub_drops_nul_byte() -> None:
    assert _scrub("foo\x00bar") == "foobar"
    assert _scrub("") == ""
    assert _scrub(None) is None  # type: ignore[arg-type]


def test_decode_mime_header_rfc2047() -> None:
    raw = "=?UTF-8?B?0J/RgNC40LLQtdGCLCDQvNC40YA=?="
    assert _decode_mime_header(raw) == "Привет, мир"


def test_decode_mime_header_plain_passthrough() -> None:
    assert _decode_mime_header("Hello") == "Hello"
    assert _decode_mime_header(None) == ""
    assert _decode_mime_header("") == ""


def test_strip_html_drops_script_and_style() -> None:
    html = "<style>x{color:red}</style><div>Hi</div><script>alert(1)</script>"
    out = _strip_html(html)
    assert "alert" not in out and "color:red" not in out
    assert "Hi" in out


def test_parse_date_variants() -> None:
    assert _parse_date("Wed, 21 May 2026 12:30:45 +0300") is not None
    assert _parse_date(None) is None
    assert _parse_date("") is None
    assert _parse_date("not a date") is None


def test_parse_rfc822_size() -> None:
    sample = [b"1 (RFC822.SIZE 12345)", b"OK FETCH"]
    assert _parse_rfc822_size(sample) == 12345
    assert _parse_rfc822_size([]) is None
    assert _parse_rfc822_size(None) is None


def test_looks_like_email() -> None:
    assert looks_like_email("a@b.com") is True
    assert looks_like_email("no-at-symbol") is False
    assert looks_like_email("a @b.com") is False
    assert looks_like_email("") is False
    assert looks_like_email(None) is False


def test_assert_safe_host_blocks_loopback() -> None:
    with pytest.raises(MailError):
        assert_safe_host("localhost")
    with pytest.raises(MailError):
        assert_safe_host("127.0.0.1")
    with pytest.raises(MailError):
        assert_safe_host("169.254.169.254")  # AWS metadata
    with pytest.raises(MailError):
        assert_safe_host("10.0.0.1")
    with pytest.raises(MailError):
        assert_safe_host("")


def test_assert_safe_host_allows_yandex() -> None:
    # imap.yandex.ru should resolve to a public IP. If DNS fails in CI
    # this becomes a flake — accept either "passed" or a DNS error, but
    # never a "blocked as private" outcome.
    try:
        assert_safe_host("imap.yandex.ru")
    except MailError as exc:
        assert "Cannot resolve" in str(exc), f"Unexpected reject: {exc}"
