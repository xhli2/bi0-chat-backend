from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context
from app.core.exceptions import ApiError
from app.db.session import get_db_session
from app.schemas.approval import ApprovalResolveRequest, ApprovalTicketOut
from app.schemas.realtime import AgentEvent, TaskState
from app.services.approval_flow import ApprovalFlowService
from app.services.task_manager import task_manager

router = APIRouter(prefix="/collaboration", tags=["collaboration"])


class TaskHandoffRequest(BaseModel):
    reviewer_id: int | None = None
    current_operator_id: int | None = None
    handoff_reason: str | None = Field(default=None, max_length=500)
    sla_seconds: int | None = Field(default=None, ge=60, le=60 * 60 * 24 * 14)


class ToolApprovalRequest(BaseModel):
    tool_name: str = Field(min_length=1)


async def _get_owned_task(task_id: str, auth: AuthContext) -> TaskState:
    state = await task_manager.get_state(task_id)
    if state is None:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")
    if state.tenant_id != auth.tenant_id:
        raise ApiError(status_code=403, code="TASK_FORBIDDEN", detail="Task does not belong to current tenant.")
    if auth.user.id not in {state.owner_id, state.current_operator_id, state.reviewer_id, state.user_id}:
        raise ApiError(status_code=403, code="TASK_FORBIDDEN", detail="Task does not belong to current user.")
    return state


def _to_ticket_out(ticket) -> ApprovalTicketOut:
    return ApprovalTicketOut(
        id=ticket.id,
        task_id=ticket.task_id,
        session_id=ticket.session_id,
        tenant_id=ticket.tenant_id,
        tool_name=ticket.tool_name,
        status=ticket.status,
        requested_by=ticket.requested_by,
        reviewer_id=ticket.reviewer_id,
        trace_id=ticket.trace_id,
        reason=ticket.reason,
        decision_note=ticket.decision_note,
        due_at=ticket.due_at,
        decided_at=ticket.decided_at,
        requested_at=ticket.requested_at,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


@router.get("/tasks/{task_id}", response_model=TaskState)
async def get_collaboration_state(task_id: str, auth: AuthContext = Depends(get_auth_context)) -> TaskState:
    return await _get_owned_task(task_id=task_id, auth=auth)


@router.post("/tasks/{task_id}/handoff", response_model=TaskState)
async def handoff_task(
    task_id: str,
    payload: TaskHandoffRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> TaskState:
    state = await _get_owned_task(task_id=task_id, auth=auth)
    if auth.user.id not in {state.owner_id, state.current_operator_id}:
        raise ApiError(status_code=403, code="TASK_HANDOFF_FORBIDDEN", detail="Only owner/operator can handoff task.")

    updated = await task_manager.update_collaboration(
        task_id=task_id,
        reviewer_id=payload.reviewer_id,
        current_operator_id=payload.current_operator_id or payload.reviewer_id,
        handoff_reason=payload.handoff_reason,
        sla_seconds=payload.sla_seconds,
    )
    assert updated is not None
    await task_manager.emit(
        AgentEvent(
            id="placeholder",
            type="collab_update",
            task_id=task_id,
            payload={
                "owner_id": updated.owner_id,
                "reviewer_id": updated.reviewer_id,
                "current_operator_id": updated.current_operator_id,
                "handoff_reason": updated.handoff_reason,
                "sla_seconds": updated.sla_seconds,
                "updated_by": auth.user.id,
            },
        )
    )
    return updated


@router.post("/tasks/{task_id}/approve-tool", response_model=TaskState)
async def approve_tool(
    task_id: str,
    payload: ToolApprovalRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> TaskState:
    await _get_owned_task(task_id=task_id, auth=auth)
    await task_manager.approve_tool(task_id=task_id, tool_name=payload.tool_name)
    await task_manager.emit(
        AgentEvent(
            id="placeholder",
            type="collab_update",
            task_id=task_id,
            payload={"approved_tool": payload.tool_name, "approved_by": auth.user.id},
        )
    )
    state = await task_manager.get_state(task_id)
    assert state is not None
    return state


@router.get("/tasks/{task_id}/approvals", response_model=list[ApprovalTicketOut])
async def list_approval_tickets(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> list[ApprovalTicketOut]:
    await _get_owned_task(task_id=task_id, auth=auth)
    service = ApprovalFlowService(db)
    tickets = await service.list_task_tickets(task_id)
    return [_to_ticket_out(item) for item in tickets if item.tenant_id == auth.tenant_id]


@router.post("/approvals/{ticket_id}/approve", response_model=ApprovalTicketOut)
async def approve_ticket(
    ticket_id: str,
    payload: ApprovalResolveRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalTicketOut:
    service = ApprovalFlowService(db)
    ticket = await service.get_ticket(ticket_id)
    if ticket is None:
        raise ApiError(status_code=404, code="APPROVAL_TICKET_NOT_FOUND", detail="Approval ticket not found.")
    await _get_owned_task(task_id=ticket.task_id, auth=auth)
    if ticket.tenant_id != auth.tenant_id:
        raise ApiError(status_code=403, code="APPROVAL_FORBIDDEN", detail="Ticket does not belong to current tenant.")
    ticket = await service.mark_approved(ticket=ticket, reviewer_id=auth.user.id, decision_note=payload.decision_note)
    await task_manager.approve_tool(task_id=ticket.task_id, tool_name=ticket.tool_name)
    await task_manager.emit(
        AgentEvent(
            id="placeholder",
            type="collab_update",
            task_id=ticket.task_id,
            payload={"approval_ticket_id": ticket.id, "decision": "approved", "reviewer_id": auth.user.id},
        )
    )
    return _to_ticket_out(ticket)


@router.post("/approvals/{ticket_id}/reject", response_model=ApprovalTicketOut)
async def reject_ticket(
    ticket_id: str,
    payload: ApprovalResolveRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> ApprovalTicketOut:
    service = ApprovalFlowService(db)
    ticket = await service.get_ticket(ticket_id)
    if ticket is None:
        raise ApiError(status_code=404, code="APPROVAL_TICKET_NOT_FOUND", detail="Approval ticket not found.")
    await _get_owned_task(task_id=ticket.task_id, auth=auth)
    if ticket.tenant_id != auth.tenant_id:
        raise ApiError(status_code=403, code="APPROVAL_FORBIDDEN", detail="Ticket does not belong to current tenant.")
    ticket = await service.mark_rejected(ticket=ticket, reviewer_id=auth.user.id, decision_note=payload.decision_note)
    await task_manager.emit(
        AgentEvent(
            id="placeholder",
            type="collab_update",
            task_id=ticket.task_id,
            payload={"approval_ticket_id": ticket.id, "decision": "rejected", "reviewer_id": auth.user.id},
        )
    )
    return _to_ticket_out(ticket)
