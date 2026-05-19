from app.core.config import get_settings
from app.tools.adapters.http_wrapper import (
    HttpSearchWrapperInput,
    HttpSearchWrapperOutput,
    tool_http_search_wrapper,
)
from app.tools.adapters.mcp_proxy import MCPProxyCallInput, MCPProxyCallOutput, tool_mcp_proxy_call
from app.tools.builtin.core_tools import (
    SessionLookupInput,
    SessionLookupOutput,
    SummarizeChunkInput,
    SummarizeChunkOutput,
    TimeNowInput,
    TimeNowOutput,
    tool_session_lookup,
    tool_summarize_chunk,
    tool_time_now,
)
from app.tools.registry import ToolSpec, tool_registry

settings = get_settings()


def register_default_tools() -> None:
    if tool_registry.get("time_now") is not None:
        return

    tool_registry.register(
        ToolSpec(
            name="time_now",
            description="Get current UTC time in ISO format.",
            input_schema=TimeNowInput,
            output_schema=TimeNowOutput,
            required_permissions=set(),
            timeout_seconds=settings.tool_call_timeout_seconds_default,
            executor=tool_time_now,
            safe_for_public_tenant=True,
            provider="function",
            risk_level="low",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="session_lookup",
            description="Read recent session context and latest summary.",
            input_schema=SessionLookupInput,
            output_schema=SessionLookupOutput,
            required_permissions={"session:read"},
            timeout_seconds=settings.tool_call_timeout_seconds_default,
            executor=tool_session_lookup,
            safe_for_public_tenant=True,
            provider="function",
            risk_level="medium",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="summarize_chunk",
            description="Summarize a text chunk with character budget.",
            input_schema=SummarizeChunkInput,
            output_schema=SummarizeChunkOutput,
            required_permissions=set(),
            timeout_seconds=settings.tool_call_timeout_seconds_default,
            executor=tool_summarize_chunk,
            safe_for_public_tenant=True,
            provider="function",
            risk_level="low",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="http_search_wrapper",
            description="Fetch HTTP content via local adapter wrapper.",
            input_schema=HttpSearchWrapperInput,
            output_schema=HttpSearchWrapperOutput,
            required_permissions={"http:external"},
            timeout_seconds=min(settings.tool_call_timeout_seconds_default, 10),
            executor=tool_http_search_wrapper,
            safe_for_public_tenant=False,
            provider="function",
            risk_level="high",
        )
    )
    tool_registry.register(
        ToolSpec(
            name="mcp_proxy_call",
            description="Call an MCP tool via configured proxy gateway.",
            input_schema=MCPProxyCallInput,
            output_schema=MCPProxyCallOutput,
            required_permissions={"mcp:invoke"},
            timeout_seconds=settings.tool_call_timeout_seconds_default,
            executor=tool_mcp_proxy_call,
            safe_for_public_tenant=False,
            provider="mcp",
            risk_level="high",
        )
    )


register_default_tools()

__all__ = ["tool_registry", "register_default_tools"]
