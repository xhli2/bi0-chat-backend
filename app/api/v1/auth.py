from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair, UserCreate, UserPublic
from app.services.auth import authenticate_user, issue_tokens, parse_refresh_token, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserPublic)
async def register(payload: UserCreate, session: AsyncSession = Depends(get_db_session)) -> UserPublic:
    user = await register_user(session, payload)
    return UserPublic(id=user.id, email=user.email)


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db_session)) -> TokenPair:
    user = await authenticate_user(session, payload.email, payload.password)
    return issue_tokens(user.id)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> TokenPair:
    claims = parse_refresh_token(payload.refresh_token)
    requested_tenant = (x_tenant_id or claims.tenant_id).strip() or claims.tenant_id
    if requested_tenant != claims.tenant_id:
        from app.core.exceptions import ApiError

        raise ApiError(status_code=403, code="TENANT_MISMATCH", detail="Tenant header does not match token claim.")
    return issue_tokens(
        claims.user_id,
        tenant_id=claims.tenant_id,
        permissions=set(claims.permissions),
        scopes=set(claims.scopes),
    )
