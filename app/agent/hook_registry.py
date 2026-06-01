from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from app.core.tool_errors import ToolExecutionError


class HookEvent(str, Enum):
    SESSION_START = "sessionStart"
    SESSION_STOP = "sessionStop"
    SUBAGENT_START = "subagentStart"
    SUBAGENT_STOP = "subagentStop"
    PLAN_CREATED = "planCreated"
    PRE_TOOL_USE = "preToolUse"
    POST_TOOL_USE = "postToolUse"
    PRE_SCRIPT_RUN = "preScriptRun"
    POST_SCRIPT_RUN = "postScriptRun"


@dataclass
class HookContext:
    event: HookEvent
    tenant_id: str
    task_id: str
    session_id: str | None = None
    trace_id: str | None = None
    user_id: int | None = None
    agent_type: str | None = None
    subagent_id: str | None = None
    subagent_role: str | None = None
    parent_task_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_output: dict[str, Any] | None = None
    script_name: str | None = None
    workspace_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


HookHandler = Callable[[HookContext], None]


class HookRegistry:
    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[HookHandler]] = {event: [] for event in HookEvent}

    def register(self, event: HookEvent, handler: HookHandler) -> None:
        self._handlers[event].append(handler)

    def emit(self, context: HookContext) -> None:
        for handler in self._handlers.get(context.event, []):
            handler(context)

    async def emit_async(self, context: HookContext) -> None:
        self.emit(context)


hook_registry = HookRegistry()
