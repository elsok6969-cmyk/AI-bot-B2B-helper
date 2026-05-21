"""Crypto vault round-trip + error paths."""

from __future__ import annotations

import importlib
import os

import pytest
from cryptography.fernet import Fernet


def _reload_crypto(key: str | None) -> object:
    """Reload settings + crypto with a given SECRETS_KEY env."""
    if key is None:
        os.environ.pop("SECRETS_KEY", None)
    else:
        os.environ["SECRETS_KEY"] = key

    import src.config
    import src.services.crypto

    importlib.reload(src.config)
    importlib.reload(src.services.crypto)
    return src.services.crypto


def test_round_trip_with_valid_key() -> None:
    key = Fernet.generate_key().decode()
    crypto = _reload_crypto(key)
    cipher = crypto.encrypt("привет, мир")
    assert cipher != "привет, мир"
    assert crypto.decrypt(cipher) == "привет, мир"
    assert crypto.is_configured() is True


def test_encrypt_without_key_raises() -> None:
    crypto = _reload_crypto("")
    assert crypto.is_configured() is False
    with pytest.raises(crypto.SecretsKeyMissingError):
        crypto.encrypt("anything")


def test_decrypt_with_wrong_key_raises() -> None:
    key_a = Fernet.generate_key().decode()
    crypto = _reload_crypto(key_a)
    cipher = crypto.encrypt("payload")

    key_b = Fernet.generate_key().decode()
    crypto = _reload_crypto(key_b)
    with pytest.raises(RuntimeError):
        crypto.decrypt(cipher)
