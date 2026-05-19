from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


MessageRole = Literal["user", "assistant", "system", "tool"]
ContextPolicy = Literal["balanced", "recent_first", "summary_heavy"]


class SessionCreateRequest(BaseModel):
    title: str = Field(default="New Session", min_length=1, max_length=255)
    tenant_id: str = Field(default="public", min_length=1, max_length=64)
    user_id: int | None = None


class SessionOut(BaseModel):
    id: str
    tenant_id: str
    user_id: int | None
    title: str
    status: str
    last_active_at: datetime
    created_at: datetime
    updated_at: datetime


class SessionMessageOut(BaseModel):
    id: str
    session_id: str
    turn_index: int
    role: MessageRole
    content: str
    token_estimate: int
    trace_id: str | None
    is_archived: bool
    created_at: datetime


class SessionSummaryOut(BaseModel):
    id: int
    session_id: str
    summary_text: str
    summary_short: str | None
    covered_until_turn: int
    token_estimate: int
    version: int
    is_archived: bool
    created_at: datetime


class SessionMemoryOut(BaseModel):
    id: int
    session_id: str
    key: str
    value: str
    importance: int
    source_turn: int
    expires_at: datetime | None
    created_at: datetime


class SessionDiagnosticsOut(BaseModel):
    session_id: str
    message_count: int
    active_message_count: int
    latest_summary_version: int | None
    latest_summary_covered_until_turn: int | None
    memory_count: int
    archived_message_count: int


class AgentContextSnapshot(BaseModel):
    session_id: str
    context_policy: ContextPolicy = "balanced"
    summary_version: int | None = None
    context_budget_tokens: int
    context_budget_used: int
    input_tokens_by_layer: dict[str, int]
    trimmed_items_count: dict[str, int]
    summary_hit: bool
    kv_hit: bool
    instructions: str
    context_blocks: list[str]
    input_message: str
