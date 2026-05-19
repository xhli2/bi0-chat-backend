from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass
class AgentToolBinding:
    skills: list[str]
    tools: list[str]


DEFAULT_AGENT_TOOL_BINDINGS: dict[str, AgentToolBinding] = {
    "report": AgentToolBinding(skills=["echo"], tools=["session_lookup", "summarize_chunk"]),
    "research": AgentToolBinding(
        skills=["echo", "uppercase"],
        tools=["session_lookup", "http_search_wrapper", "mcp_proxy_call"],
    ),
    "supervisor": AgentToolBinding(
        skills=["echo", "uppercase"],
        tools=["session_lookup", "summarize_chunk", "http_search_wrapper", "mcp_proxy_call"],
    ),
    "echo": AgentToolBinding(skills=["echo"], tools=["time_now"]),
}


def resolve_agent_tool_binding(agent_type: str) -> AgentToolBinding:
    settings = get_settings()
    configured = settings.parsed_agent_tool_bindings
    if agent_type in configured:
        item = configured[agent_type]
        return AgentToolBinding(skills=item.get("skills", []), tools=item.get("tools", []))
    if agent_type in DEFAULT_AGENT_TOOL_BINDINGS:
        return DEFAULT_AGENT_TOOL_BINDINGS[agent_type]
    return AgentToolBinding(skills=["echo"], tools=["time_now"])
