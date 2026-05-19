from __future__ import annotations

from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.tools.schemas import ToolExecutionError


class HttpSearchWrapperInput(BaseModel):
    url: str
    timeout_seconds: int = Field(default=8, ge=1, le=20)


class HttpSearchWrapperOutput(BaseModel):
    status_code: int
    body_preview: str
    fetched_url: str


async def tool_http_search_wrapper(args: dict, _: dict) -> dict:
    settings = get_settings()
    payload = HttpSearchWrapperInput.model_validate(args)
    parsed = urlparse(payload.url)
    if parsed.scheme not in {"http", "https"}:
        raise ToolExecutionError("INVALID_URL_SCHEME", "Only http/https URLs are allowed.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ToolExecutionError("INVALID_URL_HOST", "URL host is required.")

    denied_hosts = settings.parsed_http_tool_denied_hosts
    if host in denied_hosts:
        raise ToolExecutionError("HTTP_HOST_DENIED", f"Host '{host}' is denied by policy.")

    allowed_hosts = settings.parsed_http_tool_allowed_hosts
    if allowed_hosts and host not in allowed_hosts:
        raise ToolExecutionError("HTTP_HOST_NOT_ALLOWED", f"Host '{host}' is not in allowed hosts policy.")

    async with httpx.AsyncClient(timeout=payload.timeout_seconds) as client:
        response = await client.get(payload.url)
    max_bytes = max(256, settings.http_tool_max_response_bytes)
    body = response.text[:max_bytes]
    return HttpSearchWrapperOutput(
        status_code=response.status_code,
        body_preview=body,
        fetched_url=payload.url,
    ).model_dump(mode="python")
