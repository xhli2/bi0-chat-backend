from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


EventType = Literal[
    "status",
    "delta",
    "part",
    "usage",
    "tool_start",
    "tool_end",
    "tool_error",
    "handoff_start",
    "handoff_end",
    "approval_required",
    "step_timeout",
    "checkpoint_saved",
    "collab_update",
]
TaskStatus = Literal["queued", "running", "success", "failed", "cancelled"]
TaskPriority = Literal["low", "default", "high"]


class AgentEvent(BaseModel):
    id: str
    type: EventType
    task_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any]


class TaskState(BaseModel):
    task_id: str
    status: TaskStatus
    priority: TaskPriority = "default"
    model: str | None = None
    session_id: str | None = None
    user_id: int | None = None
    context_policy: str = "balanced"
    tenant_id: str = "public"
    trace_id: str | None = None
    retry_count: int = 0
    poison: bool = False
    failure_reason: str | None = None
    interrupted: bool = False
    latest_event_id: str | None = None
    owner_id: int | None = None
    reviewer_id: int | None = None
    current_operator_id: int | None = None
    handoff_reason: str | None = None
    sla_seconds: int | None = None
    awaiting_approval: bool = False
    workflow_step: int = 0
