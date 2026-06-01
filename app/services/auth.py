from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import TokenPair, UserCreate


@dataclass
class TokenClaims:
    user_id: int
    tenant_id: str
    permissions: set[str]
    scopes: set[str]


async def register_user(session: AsyncSession, payload: UserCreate) -> User:
    existing = await session.execute(select(User).where(User.email == payload.email))
    if existing.scalars().first():
        raise ApiError(status_code=409, code="EMAIL_CONFLICT", detail="Email already exists.")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if user is None or not verify_password(password, user.hashed_password):
        raise ApiError(status_code=401, code="INVALID_CREDENTIALS", detail="Invalid email or password.")
    return user


def issue_tokens(
    user_id: int,
    tenant_id: str | None = None,
    permissions: set[str] | None = None,
    scopes: set[str] | None = None,
) -> TokenPair:
    settings = get_settings()
    effective_tenant = (tenant_id or settings.access_token_default_tenant).strip() or "public"
    tenant_defaults = settings.parsed_tenant_default_permissions.get(effective_tenant, set())
    effective_permissions = set(permissions or tenant_defaults or settings.parsed_access_token_default_permissions)
    effective_scopes = set(scopes or settings.parsed_access_token_default_scopes)

    subject = str(user_id)
    claims = {
        "tenant_id": effective_tenant,
        "permissions": sorted(effective_permissions),
        "scopes": sorted(effective_scopes),
    }
    return TokenPair(
        access_token=create_access_token(subject, extra_claims=claims),
        refresh_token=create_refresh_token(subject, extra_claims=claims),
    )


def parse_access_token(token: str) -> TokenClaims:
    payload = decode_token(token, "access")
    return TokenClaims(
        user_id=int(payload["sub"]),
        tenant_id=str(payload.get("tenant_id", "public")),
        permissions={item for item in payload.get("permissions", []) if isinstance(item, str)},
        scopes={item for item in payload.get("scopes", []) if isinstance(item, str)},
    )


def parse_refresh_token(token: str) -> TokenClaims:
    payload = decode_token(token, "refresh")
    return TokenClaims(
        user_id=int(payload["sub"]),
        tenant_id=str(payload.get("tenant_id", "public")),
        permissions={item for item in payload.get("permissions", []) if isinstance(item, str)},
        scopes={item for item in payload.get("scopes", []) if isinstance(item, str)},
    )
