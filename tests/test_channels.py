"""Channel routing helpers — text scrubbing + length caps."""

from __future__ import annotations

from src.db.models import DraftChannel
from src.services.channels import _safe_text


def test_safe_text_strips_nul() -> None:
    assert _safe_text("foo\x00bar", channel=DraftChannel.TELETHON_USER) == "foobar"


def test_safe_text_caps_telegram() -> None:
    out = _safe_text("x" * 10000, channel=DraftChannel.TELETHON_USER)
    assert len(out) <= 4096
    assert out.endswith("(обрезано)")


def test_safe_text_caps_email_higher() -> None:
    out = _safe_text("x" * 200_000, channel=DraftChannel.EMAIL)
    assert len(out) <= 100_000
    assert out.endswith("(обрезано)")


def test_safe_text_passes_through_short() -> None:
    assert _safe_text("hi", channel=DraftChannel.TELETHON_USER) == "hi"
