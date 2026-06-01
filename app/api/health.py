from datetime import datetime, timezone

from fastapi import APIRouter, Response
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(response: Response) -> dict[str, str | dict[str, str]]:
    if settings.environment == "test":
        return {
            "status": "ready",
            "time": datetime.now(timezone.utc).isoformat(),
            "checks": {"database": "skipped", "redis": "skipped"},
        }

    checks: dict[str, str] = {"database": "ok"}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"
        response.status_code = 503

    if settings.environment != "test":
        try:
            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            try:
                if not await redis.ping():
                    checks["redis"] = "error: ping failed"
                    response.status_code = 503
                else:
                    checks["redis"] = "ok"
            finally:
                await redis.aclose()
        except Exception as exc:
            checks["redis"] = f"error: {exc.__class__.__name__}"
            response.status_code = 503

    body: dict[str, str | dict[str, str]] = {
        "status": "ready" if response.status_code == 200 else "not_ready",
        "time": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
    return body
