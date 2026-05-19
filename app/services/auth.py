from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import TokenPair, UserCreate


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


def issue_tokens(user_id: int) -> TokenPair:
    subject = str(user_id)
    return TokenPair(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )


def parse_access_token(token: str) -> int:
    payload = decode_token(token, "access")
    return int(payload["sub"])


def parse_refresh_token(token: str) -> int:
    payload = decode_token(token, "refresh")
    return int(payload["sub"])
