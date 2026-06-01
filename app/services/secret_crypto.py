from __future__ import annotations

from app.core.config import get_settings


def encrypt_provider_secret(secret: str) -> str | None:
    settings = get_settings()
    if not settings.provider_secret_fallback_enabled:
        return None
    key = settings.provider_secret_fallback_fernet_key.strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        token = Fernet(key.encode("utf-8")).encrypt(secret.encode("utf-8"))
        return token.decode("utf-8")
    except Exception:
        return None


def decrypt_provider_secret(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    settings = get_settings()
    if not settings.provider_secret_fallback_enabled:
        return None
    key = settings.provider_secret_fallback_fernet_key.strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        plain = Fernet(key.encode("utf-8")).decrypt(ciphertext.encode("utf-8"))
        return plain.decode("utf-8")
    except Exception:
        return None
