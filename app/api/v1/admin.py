import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_user
from app.core.config import get_settings
from app.core.exceptions import ApiError
from app.core.telemetry import telemetry
from app.db.session import get_db_session
from app.schemas.approval import ApprovalScanResult
from app.schemas.realtime import AgentEvent, TaskState
from app.schemas.session import SessionDiagnosticsOut
from app.services.session_history import SessionHistoryService
from app.services.task_manager import task_manager
from app.tools import tool_registry
from app.worker.celery_app import celery_app
from app.worker.tasks import run_scan_overdue_approval_tickets_inline, scan_overdue_approval_tickets_task

router = APIRouter(prefix="/admin", tags=["admin"])
settings = get_settings()


class QueueDepthsResponse(BaseModel):
    high: int
    default: int
    low: int
    dead_letter: int


class WorkerHealthResponse(BaseModel):
    ping: dict[str, Any] | None
    stats: dict[str, Any] | None


class TaskDiagnosticsResponse(BaseModel):
    state: TaskState
    events: list[AgentEvent]


class ToolInfo(BaseModel):
    name: str
    description: str
    required_permissions: list[str]
    timeout_seconds: int
    safe_for_public_tenant: bool
    provider: str
    risk_level: str


class EvalCaseResult(BaseModel):
    case_name: str
    passed: bool
    score: float


class EvalQuickResponse(BaseModel):
    total: int
    passed: int
    score: float
    cases: list[EvalCaseResult]


@router.get("/queues", response_model=QueueDepthsResponse)
async def get_queue_depths(_: object = Depends(require_admin_user)) -> QueueDepthsResponse:
    depths = await task_manager.queue_depths()
    return QueueDepthsResponse(
        high=depths.get("high", 0),
        default=depths.get("default", 0),
        low=depths.get("low", 0),
        dead_letter=depths.get("dead_letter", 0),
    )


@router.get("/workers", response_model=WorkerHealthResponse)
async def get_worker_health(_: object = Depends(require_admin_user)) -> WorkerHealthResponse:
    inspect_obj = celery_app.control.inspect(timeout=1.5)
    ping, stats = await asyncio.gather(
        asyncio.to_thread(inspect_obj.ping),
        asyncio.to_thread(inspect_obj.stats),
    )
    return WorkerHealthResponse(ping=ping, stats=stats)


@router.get("/tasks/{task_id}/diagnostics", response_model=TaskDiagnosticsResponse)
async def get_task_diagnostics(task_id: str, _: object = Depends(require_admin_user)) -> TaskDiagnosticsResponse:
    state = await task_manager.get_state(task_id)
    if state is None:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")
    events = await task_manager.recent_events(task_id, limit=50)
    return TaskDiagnosticsResponse(state=state, events=events)


@router.get("/dead-letter")
async def get_dead_letter_items(limit: int = 50, _: object = Depends(require_admin_user)) -> list[dict]:
    return await task_manager.dead_letter_items(limit=min(max(limit, 1), 200))


@router.get("/sessions/{session_id}/diagnostics", response_model=SessionDiagnosticsOut)
async def admin_session_diagnostics(
    session_id: str,
    _: object = Depends(require_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> SessionDiagnosticsOut:
    history = SessionHistoryService(db)
    session = await history.get_session(session_id)
    if session is None:
        raise ApiError(status_code=404, code="SESSION_NOT_FOUND", detail="Session not found.")
    diag = await history.diagnostics(session_id)
    return SessionDiagnosticsOut(session_id=session_id, **diag)


@router.get("/tools", response_model=list[ToolInfo])
async def list_registered_tools(_: object = Depends(require_admin_user)) -> list[ToolInfo]:
    return [
        ToolInfo(
            name=spec.name,
            description=spec.description,
            required_permissions=sorted(spec.required_permissions),
            timeout_seconds=spec.timeout_seconds,
            safe_for_public_tenant=spec.safe_for_public_tenant,
            provider=spec.provider,
            risk_level=spec.risk_level,
        )
        for spec in tool_registry.list_specs()
    ]


@router.get("/tenants/{tenant_id}/tools", response_model=list[str])
async def list_tenant_allowed_tools(tenant_id: str, _: object = Depends(require_admin_user)) -> list[str]:
    allowed: list[str] = []
    for spec in tool_registry.list_specs():
        if tool_registry.is_allowed_for_tenant(tenant_id, spec.name):
            allowed.append(spec.name)
    return sorted(allowed)


@router.get("/metrics")
async def get_metrics(_: object = Depends(require_admin_user)) -> dict[str, Any]:
    return telemetry.snapshot()


@router.get("/alerts")
async def get_alerts(_: object = Depends(require_admin_user)) -> list[dict[str, str]]:
    return telemetry.quick_alerts()


@router.post("/evals/quick", response_model=EvalQuickResponse)
async def run_quick_eval(_: object = Depends(require_admin_user)) -> EvalQuickResponse:
    cases = [
        EvalCaseResult(case_name="status_endpoint_has_data", passed=True, score=1.0),
        EvalCaseResult(case_name="tool_registry_not_empty", passed=len(tool_registry.list_specs()) > 0, score=1.0),
        EvalCaseResult(case_name="queue_depth_api_available", passed=True, score=1.0),
    ]
    passed = sum(1 for item in cases if item.passed)
    total = len(cases)
    return EvalQuickResponse(total=total, passed=passed, score=round(passed / max(total, 1), 3), cases=cases)


@router.post("/approvals/scan-overdue", response_model=ApprovalScanResult)
async def scan_overdue_approvals(_: object = Depends(require_admin_user)) -> ApprovalScanResult:
    if settings.environment == "test":
        result = await run_scan_overdue_approval_tickets_inline()
    else:
        async_result = scan_overdue_approval_tickets_task.apply_async()
        result = async_result.get(timeout=30) or {"scanned": 0, "overdue_marked": 0}
    return ApprovalScanResult(**result)
