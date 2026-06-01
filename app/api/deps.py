from dataclasses import dataclass

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.user import User
from app.services.auth import parse_access_token

bearer_scheme = HTTPBearer(auto_error=False)
settings = get_settings()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    if credentials is None:
        raise ApiError(status_code=401, code="UNAUTHORIZED", detail="Missing bearer token.")
    token_claims = parse_access_token(credentials.credentials)
    user_id = token_claims.user_id
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if user is None:
        raise ApiError(status_code=401, code="USER_NOT_FOUND", detail="User in token does not exist.")
    return user


@dataclass
class AuthContext:
    user: User
    tenant_id: str
    trace_id: str | None
    permissions: set[str]
    scopes: set[str]


async def get_auth_context(
    user: User = Depends(get_current_user),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_trace_id: str | None = Header(default=None, alias="X-Trace-ID"),
) -> AuthContext:
    token_claims = parse_access_token(credentials.credentials) if credentials else None
    claim_tenant = token_claims.tenant_id if token_claims else "public"
    header_tenant = (x_tenant_id or claim_tenant).strip() or claim_tenant
    if header_tenant != claim_tenant:
        raise ApiError(status_code=403, code="TENANT_MISMATCH", detail="Tenant header does not match token claim.")
    return AuthContext(
        user=user,
        tenant_id=claim_tenant,
        trace_id=x_trace_id,
        permissions=set(token_claims.permissions if token_claims else []),
        scopes=set(token_claims.scopes if token_claims else []),
    )


async def require_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.id not in settings.parsed_admin_user_ids:
        raise ApiError(status_code=403, code="ADMIN_REQUIRED", detail="Admin permission required.")
    return user
