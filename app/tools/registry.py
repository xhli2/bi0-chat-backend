from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from app.core.config import get_settings

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel] | None
    required_permissions: set[str]
    timeout_seconds: int
    executor: ToolHandler
    safe_for_public_tenant: bool = True
    provider: Literal["function", "mcp"] = "function"
    risk_level: Literal["low", "medium", "high"] = "low"


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._settings = get_settings()

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def list_specs(self) -> list[ToolSpec]:
        return sorted(self._specs.values(), key=lambda s: s.name)

    def list_for_agent(self, agent_tool_names: list[str]) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for name in agent_tool_names:
            spec = self.get(name)
            if spec is not None:
                specs.append(spec)
        return specs

    def is_allowed_for_tenant(self, tenant_id: str, tool_name: str) -> bool:
        spec = self.get(tool_name)
        if spec is None:
            return False
        if tenant_id == "public" and not spec.safe_for_public_tenant:
            return False

        policies = self._settings.parsed_tenant_tool_policies
        tenant_tools = policies.get(tenant_id)
        if tenant_tools is None:
            return True
        if "*" in tenant_tools:
            return True
        return tool_name in tenant_tools


tool_registry = ToolRegistry()
