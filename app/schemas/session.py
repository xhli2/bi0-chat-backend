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
    task_id: str | None = None
    metadata: dict = Field(default_factory=dict)
    is_archived: bool
    created_at: datetime


class SessionListOut(BaseModel):
    items: list[SessionOut]
    total: int


class TokenUsageBreakdown(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    message_token_estimate: int = 0
    run_count: int = 0


class SessionTokenUsageOut(BaseModel):
    session_id: str
    usage: TokenUsageBreakdown


class UserTokenUsageOut(BaseModel):
    tenant_id: str
    user_id: int
    session_count: int
    usage: TokenUsageBreakdown
    by_session: list[SessionTokenUsageOut] = Field(default_factory=list)


class SessionRunOut(BaseModel):
    id: str
    session_id: str
    turn_index: int | None
    trace_id: str | None
    agent_type: str
    model: str | None
    context_policy: str | None
    status: str
    usage: dict | None = None
    resolved_skills: list[str] = Field(default_factory=list)
    context_pack_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class SessionToolCallOut(BaseModel):
    id: str
    session_id: str
    turn_index: int
    task_id: str
    tool_name: str
    call_id: str
    status: str
    input_json: dict = Field(default_factory=dict)
    output_json: dict | None = None
    output_ref: str | None = None
    duration_ms: int | None = None
    created_at: datetime


class SessionEntityOut(BaseModel):
    id: int
    session_id: str
    entity_type: str
    canonical_id: str
    display_name: str | None
    genome_build: str | None
    source: str | None
    source_turn: int | None
    summary: str | None
    raw_ref: str | None
    is_active: bool
    created_at: datetime


class SessionArtifactOut(BaseModel):
    id: str
    session_id: str
    turn_index: int | None
    task_id: str
    run_id: str | None
    kind: str
    filename: str | None
    storage_path: str
    mime_type: str | None
    sha256: str | None
    size_bytes: int | None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class SessionTimelineOut(BaseModel):
    session_id: str
    messages: list[SessionMessageOut]
    runs: list[SessionRunOut]
    tool_calls: list[SessionToolCallOut]
    entities: list[SessionEntityOut]
    artifacts: list[SessionArtifactOut] = Field(default_factory=list)
    token_usage: TokenUsageBreakdown


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
    context_pack_ids: list[str] = []
    skill_names: list[str] = []
