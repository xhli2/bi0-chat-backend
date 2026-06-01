import pytest
from httpx import AsyncClient

from app.services.auth import issue_tokens
from app.tools.adapters.http_wrapper import tool_http_search_wrapper
from app.tools.adapters.mcp_proxy import tool_mcp_proxy_call
from app.tools.schemas import ToolExecutionError


@pytest.mark.asyncio
async def test_tenant_mismatch_rejected(client: AsyncClient):
    register_response = await client.post(
        "/api/v1/auth/register",
        json={"email": "tenant-mismatch@example.com", "password": "password123"},
    )
    assert register_response.status_code == 200
    user_id = register_response.json()["id"]
    token = issue_tokens(user_id=user_id, tenant_id="lab-a", permissions={"session:read"}, scopes={"agent:run"}).access_token
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": "lab-b"}
    response = await client.get("/api/v1/agents/skills", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "TENANT_MISMATCH"


@pytest.mark.asyncio
async def test_http_wrapper_default_deny_when_allowlist_empty():
    with pytest.raises(ToolExecutionError) as exc:
        await tool_http_search_wrapper({"url": "https://example.org"}, {})
    assert exc.value.code == "HTTP_HOST_POLICY_EMPTY"


@pytest.mark.asyncio
async def test_mcp_proxy_default_deny_when_allowlist_empty():
    with pytest.raises(ToolExecutionError) as exc:
        await tool_mcp_proxy_call({"server": "default", "tool": "ping", "arguments": {}}, {})
    assert exc.value.code == "MCP_POLICY_EMPTY"
