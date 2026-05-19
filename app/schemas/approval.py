from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "overdue"]


class ApprovalTicketOut(BaseModel):
    id: str
    task_id: str
    session_id: str | None
    tenant_id: str
    tool_name: str
    status: ApprovalStatus
    requested_by: int | None
    reviewer_id: int | None
    trace_id: str | None
    reason: str | None
    decision_note: str | None
    due_at: datetime | None
    decided_at: datetime | None
    requested_at: datetime
    created_at: datetime
    updated_at: datetime


class ApprovalResolveRequest(BaseModel):
    decision_note: str | None = Field(default=None, max_length=1000)


class ApprovalScanResult(BaseModel):
    scanned: int
    overdue_marked: int
