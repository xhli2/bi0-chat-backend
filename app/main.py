from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, request_log_middleware
from app.db.session import engine
from app.models import Base

settings = get_settings()
configure_logging()

_INSECURE_JWT_SECRETS = {"dev-insecure-secret-change-me", "change-me", ""}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.environment != "test" and settings.jwt_secret_key.strip() in _INSECURE_JWT_SECRETS:
        raise RuntimeError("JWT_SECRET_KEY must be set to a strong value outside test environments.")
    if settings.environment == "test":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
register_exception_handlers(app)
app.middleware("http")(request_log_middleware)

if settings.parsed_cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.parsed_cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(api_router)
