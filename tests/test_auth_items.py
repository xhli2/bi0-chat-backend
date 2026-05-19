import pytest


async def _register_and_login(client):
    email = "user@example.com"
    password = "password123"
    register_response = await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert register_response.status_code == 200

    login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login_response.status_code == 200
    tokens = login_response.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}, tokens


@pytest.mark.asyncio
async def test_auth_refresh(client):
    _, tokens = await _register_and_login(client)
    refresh_response = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_response.status_code == 200
    body = refresh_response.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body


@pytest.mark.asyncio
async def test_item_crud_pagination_soft_delete(client):
    headers, _ = await _register_and_login(client)
    create_response = await client.post(
        "/api/v1/items",
        json={"title": "My First Item", "description": "desc"},
        headers=headers,
    )
    assert create_response.status_code == 200
    item = create_response.json()
    item_id = item["id"]

    list_response = await client.get("/api/v1/items?page=1&size=10&q=First", headers=headers)
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["total"] == 1
    assert list_body["items"][0]["id"] == item_id

    update_response = await client.patch(f"/api/v1/items/{item_id}", json={"title": "Renamed"}, headers=headers)
    assert update_response.status_code == 200
    assert update_response.json()["title"] == "Renamed"

    delete_response = await client.delete(f"/api/v1/items/{item_id}", headers=headers)
    assert delete_response.status_code == 200

    list_after_delete = await client.get("/api/v1/items", headers=headers)
    assert list_after_delete.status_code == 200
    assert list_after_delete.json()["total"] == 0

    restore_response = await client.post(f"/api/v1/items/{item_id}/restore", headers=headers)
    assert restore_response.status_code == 200
    assert restore_response.json()["id"] == item_id
