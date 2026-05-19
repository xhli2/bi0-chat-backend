from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


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


class ToolExecutionError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class EmptyInput(BaseModel):
    pass
