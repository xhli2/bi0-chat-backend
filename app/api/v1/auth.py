from fastapi import APIRouter, Depends
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
async def refresh(payload: RefreshRequest) -> TokenPair:
    user_id = parse_refresh_token(payload.refresh_token)
    return issue_tokens(user_id)
