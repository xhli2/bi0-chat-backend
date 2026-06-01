import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET_KEY"] = "test-secret"
os.environ["DEFAULT_LLM_MODEL"] = ""
os.environ["DEFAULT_PROVIDER_BASE_URL"] = ""
os.environ["DEFAULT_PROVIDER_API_KEY"] = ""
os.environ["DEFAULT_PROVIDER_NAME"] = ""
os.environ["MODEL_ROUTER_SIMPLE_MODEL"] = "gpt-4.1-mini"
os.environ["MODEL_ROUTER_COMPLEX_MODEL"] = "gpt-4.1"
os.environ["MODEL_ALLOWLIST"] = "builtin,gpt-4.1-mini,gpt-4.1,auto"
os.environ["TENANT_MODEL_POLICIES_JSON"] = '{"public":["builtin","gpt-4.1-mini"],"vip":["builtin","gpt-4.1-mini","gpt-4.1"]}'
os.environ["ACCESS_TOKEN_DEFAULT_TENANT"] = "public"

from app.main import app  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> AsyncGenerator[None, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    email = "agent@example.com"
    password = "password123"
    register_response = await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert register_response.status_code == 200
    login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}
