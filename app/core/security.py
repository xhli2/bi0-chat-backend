from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.exceptions import ApiError

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def _create_token(subject: str, expires_delta: timedelta, token_type: str, extra_claims: dict | None = None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": subject, "type": token_type, "iat": now, "exp": now + expires_delta}
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, extra_claims: dict | None = None) -> str:
    settings = get_settings()
    return _create_token(
        subject,
        timedelta(minutes=settings.jwt_access_token_expire_minutes),
        "access",
        extra_claims=extra_claims,
    )


def create_refresh_token(subject: str, extra_claims: dict | None = None) -> str:
    settings = get_settings()
    return _create_token(
        subject,
        timedelta(minutes=settings.jwt_refresh_token_expire_minutes),
        "refresh",
        extra_claims=extra_claims,
    )


def decode_token(token: str, expected_type: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ApiError(status_code=401, code="INVALID_TOKEN", detail="Token is invalid.") from exc

    if payload.get("type") != expected_type:
        raise ApiError(status_code=401, code="INVALID_TOKEN_TYPE", detail="Token type is invalid.")
    return payload
