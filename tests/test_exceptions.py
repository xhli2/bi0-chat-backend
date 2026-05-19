import pytest


@pytest.mark.asyncio
async def test_unauthorized_error_envelope(client):
    response = await client.get("/api/v1/items")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "UNAUTHORIZED"
    assert body["error"] == "API_ERROR"
    assert "timestamp" in body
