from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.tools.schemas import ToolExecutionError


class MCPProxyCallInput(BaseModel):
    server: str = Field(default_factory=lambda: get_settings().mcp_proxy_default_server, min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class MCPProxyCallOutput(BaseModel):
    server: str
    tool: str
    result: Any


async def tool_mcp_proxy_call(args: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    payload = MCPProxyCallInput.model_validate(args)
    allowed_servers = settings.parsed_mcp_allowed_servers
    if not allowed_servers:
        raise ToolExecutionError(
            "MCP_POLICY_EMPTY",
            "MCP allowlist is empty; outbound MCP calls are disabled.",
            retryable=False,
        )
    if "*" not in allowed_servers and payload.server not in allowed_servers:
        raise ToolExecutionError(
            "MCP_SERVER_NOT_ALLOWED",
            f"MCP server '{payload.server}' is not allowed by policy.",
            retryable=False,
        )

    url = settings.mcp_proxy_base_url.rstrip("/") + "/tools/call"
    body = {"server": payload.server, "toolName": payload.tool, "arguments": payload.arguments}
    try:
        async with httpx.AsyncClient(timeout=settings.mcp_proxy_timeout_seconds) as client:
            response = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise ToolExecutionError("MCP_PROXY_UNREACHABLE", f"MCP proxy request failed: {exc}", retryable=True) from exc

    if response.status_code >= 400:
        raise ToolExecutionError(
            "MCP_PROXY_ERROR",
            f"MCP proxy returned {response.status_code}: {response.text[:500]}",
            retryable=response.status_code >= 500,
        )

    data = response.json()
    return MCPProxyCallOutput(server=payload.server, tool=payload.tool, result=data).model_dump(mode="python")
