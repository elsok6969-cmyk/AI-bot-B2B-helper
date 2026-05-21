from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from src.config import settings


class SecretsKeyMissingError(RuntimeError):
    """Raised when SECRETS_KEY is not configured but encryption was requested."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.secrets_key.get_secret_value().strip()
    if not key:
        raise SecretsKeyMissingError(
            "SECRETS_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`"
            " and add it to .env."
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Failed to decrypt secret — wrong SECRETS_KEY?") from exc


def is_configured() -> bool:
    return bool(settings.secrets_key.get_secret_value().strip())
