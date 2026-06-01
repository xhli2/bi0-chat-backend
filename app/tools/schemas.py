from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.core.tool_errors import ToolExecutionError

__all__ = ["ToolExecutionResult", "ToolExecutionContext", "ToolExecutionError", "EmptyInput"]


class ToolExecutionResult(BaseModel):
    ok: bool
    data: Any | None = None
    error_code: str | None = None
    message: str | None = None
    retryable: bool = False
    duration_ms: int = 0


@dataclass
class ToolExecutionContext:
    tenant_id: str
    user_id: int | None
    session_id: str | None
    trace_id: str | None
    task_id: str
    permissions: set[str] = field(default_factory=set)
    approved_tools: set[str] = field(default_factory=set)
    scopes: set[str] = field(default_factory=set)
    tool_result_cache: dict[str, Any] = field(default_factory=dict)
    allowed_tools: set[str] | None = None


class EmptyInput(BaseModel):
    pass
