import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_sessions(client: AsyncClient, auth_headers: dict[str, str]):
    create = await client.post("/api/v1/sessions", headers=auth_headers, json={"title": "Bio session"})
    assert create.status_code == 200
    session_id = create.json()["id"]

    listed = await client.get("/api/v1/sessions", headers=auth_headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 1
    assert any(item["id"] == session_id for item in body["items"])


@pytest.mark.asyncio
async def test_session_token_usage_empty(client: AsyncClient, auth_headers: dict[str, str]):
    create = await client.post("/api/v1/sessions", headers=auth_headers, json={"title": "Usage test"})
    session_id = create.json()["id"]

    response = await client.get(f"/api/v1/sessions/{session_id}/token-usage", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["usage"]["run_count"] == 0
    assert body["usage"]["message_token_estimate"] == 0


@pytest.mark.asyncio
async def test_user_token_usage(client: AsyncClient, auth_headers: dict[str, str]):
    await client.post("/api/v1/sessions", headers=auth_headers, json={"title": "One"})
    response = await client.get("/api/v1/sessions/token-usage", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["session_count"] >= 1
    assert "usage" in body
    assert isinstance(body["by_session"], list)


@pytest.mark.asyncio
async def test_session_timeline(client: AsyncClient, auth_headers: dict[str, str]):
    create = await client.post("/api/v1/sessions", headers=auth_headers, json={"title": "Timeline"})
    session_id = create.json()["id"]

    response = await client.get(f"/api/v1/sessions/{session_id}/timeline", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert isinstance(body["messages"], list)
    assert isinstance(body["runs"], list)
    assert isinstance(body["tool_calls"], list)
    assert isinstance(body["entities"], list)


@pytest.mark.asyncio
async def test_session_runs_requires_ownership(client: AsyncClient, auth_headers: dict[str, str]):
    create = await client.post("/api/v1/sessions", headers=auth_headers, json={"title": "Private"})
    session_id = create.json()["id"]

    other_email = "other@example.com"
    await client.post("/api/v1/auth/register", json={"email": other_email, "password": "password123"})
    other_login = await client.post("/api/v1/auth/login", json={"email": other_email, "password": "password123"})
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    response = await client.get(f"/api/v1/sessions/{session_id}/runs", headers=other_headers)
    assert response.status_code == 403
