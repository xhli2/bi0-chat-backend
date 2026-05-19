from uuid import uuid4

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api.deps import AuthContext, get_auth_context
from app.core.exceptions import ApiError
from app.core.config import get_settings
from app.realtime.sse import create_sse_response
from app.schemas.realtime import AgentEvent, TaskPriority, TaskState
from app.services.task_manager import task_manager
from app.worker.tasks import run_demo_task, run_demo_task_inline

router = APIRouter(prefix="/tasks", tags=["tasks"])
settings = get_settings()


class CreateTaskRequest(BaseModel):
    priority: TaskPriority = "default"
    model: str | None = None


@router.post("", response_model=TaskState)
async def create_background_task(
    payload: CreateTaskRequest = CreateTaskRequest(),
    auth: AuthContext = Depends(get_auth_context),
) -> TaskState:
    trace_id = str(uuid4())
    task_id = await task_manager.create_task(
        priority=payload.priority,
        model=payload.model,
        tenant_id=auth.tenant_id,
        trace_id=trace_id,
        user_id=auth.user.id,
        owner_id=auth.user.id,
    )
    if settings.environment == "test":
        await run_demo_task_inline(task_id=task_id)
    else:
        run_demo_task.apply_async(
            kwargs={"task_id": task_id, "trace_id": trace_id},
            queue=payload.priority,
            routing_key=payload.priority,
        )
    state = await task_manager.get_state(task_id)
    assert state is not None
    return state


@router.get("/{task_id}", response_model=TaskState)
async def get_task_state(task_id: str, auth: AuthContext = Depends(get_auth_context)) -> TaskState:
    state = await task_manager.get_state(task_id)
    if state is None:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")
    if state.tenant_id != auth.tenant_id or state.user_id != auth.user.id:
        raise ApiError(status_code=403, code="TASK_FORBIDDEN", detail="Task does not belong to current user.")
    return state


@router.post("/{task_id}/cancel", response_model=TaskState)
async def cancel_task(task_id: str, auth: AuthContext = Depends(get_auth_context)) -> TaskState:
    state = await task_manager.get_state(task_id)
    if state is None:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")
    if state.tenant_id != auth.tenant_id or state.user_id != auth.user.id:
        raise ApiError(status_code=403, code="TASK_FORBIDDEN", detail="Task does not belong to current user.")
    interrupted = await task_manager.interrupt(task_id)
    if not interrupted:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")
    await task_manager.emit(
        AgentEvent(id="cancelled", type="status", task_id=task_id, payload={"status": "cancelled", "message": "cancelled by user"})
    )
    state = await task_manager.get_state(task_id)
    assert state is not None
    return state


@router.get("/{task_id}/stream")
async def stream_task(
    task_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    auth: AuthContext = Depends(get_auth_context),
):
    state = await task_manager.get_state(task_id)
    if state is None:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")
    if state.tenant_id != auth.tenant_id or state.user_id != auth.user.id:
        raise ApiError(status_code=403, code="TASK_FORBIDDEN", detail="Task does not belong to current user.")
    return create_sse_response(task_manager.stream_events(task_id, last_event_id))
