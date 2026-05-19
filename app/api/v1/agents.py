from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context
from app.agent.skills import list_skills
from app.db.session import get_db_session
from app.schemas.realtime import TaskPriority, TaskState
from app.schemas.session import ContextPolicy
from app.services.model_policy import validate_model_for_tenant
from app.services.session_history import SessionHistoryService, normalize_context_policy
from app.services.task_manager import task_manager
from app.worker.tasks import run_agent_task, run_agent_task_inline
from app.core.config import get_settings

router = APIRouter(prefix="/agents", tags=["agents"])
settings = get_settings()


class AgentRunRequest(BaseModel):
    agent_type: str = "echo"
    prompt: str
    model: str = Field(default="builtin", description="Selected model name from frontend.")
    priority: TaskPriority = "default"
    session_id: str | None = None
    context_policy: ContextPolicy = "balanced"


class AgentRunResponse(BaseModel):
    task_id: str
    stream_url: str
    status_url: str
    queue: TaskPriority
    model: str
    session_id: str
    context_policy: ContextPolicy
    tenant_id: str
    trace_id: str


class AgentResumeRequest(BaseModel):
    resume_from_step: int | None = Field(default=None, ge=0)
    approved_tool: str | None = None


@router.get("/skills", response_model=list[str])
async def get_supported_skills(_: AuthContext = Depends(get_auth_context)) -> list[str]:
    return list_skills()


@router.post("/run", response_model=AgentRunResponse)
async def run_agent(
    payload: AgentRunRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db_session),
) -> AgentRunResponse:
    tenant_id = auth.tenant_id
    trace_id = auth.trace_id or str(uuid4())
    user_id = auth.user.id
    context_policy = normalize_context_policy(payload.context_policy)
    validate_model_for_tenant(model=payload.model, tenant_id=tenant_id)

    history = SessionHistoryService(db)
    session = await history.ensure_session(payload.session_id, tenant_id=tenant_id, user_id=user_id)

    task_id = await task_manager.create_task(
        priority=payload.priority,
        model=payload.model,
        tenant_id=tenant_id,
        trace_id=trace_id,
        session_id=session.id,
        user_id=user_id,
        context_policy=context_policy,
        owner_id=user_id,
        current_operator_id=user_id,
    )
    await task_manager.save_run_spec(
        task_id,
        {
            "agent_type": payload.agent_type,
            "prompt": payload.prompt,
            "model": payload.model,
            "tenant_id": tenant_id,
            "trace_id": trace_id,
            "session_id": session.id,
            "user_id": user_id,
            "context_policy": context_policy,
            "priority": payload.priority,
        },
    )
    if settings.environment == "test":
        await run_agent_task_inline(
            task_id=task_id,
            agent_type=payload.agent_type,
            prompt=payload.prompt,
            model=payload.model,
            tenant_id=tenant_id,
            trace_id=trace_id,
            session_id=session.id,
            user_id=user_id,
            context_policy=context_policy,
        )
    else:
        run_agent_task.apply_async(
            kwargs={
                "task_id": task_id,
                "agent_type": payload.agent_type,
                "prompt": payload.prompt,
                "model": payload.model,
                "tenant_id": tenant_id,
                "trace_id": trace_id,
                "session_id": session.id,
                "user_id": user_id,
                "context_policy": context_policy,
            },
            queue=payload.priority,
            routing_key=payload.priority,
        )
    return AgentRunResponse(
        task_id=task_id,
        stream_url=f"/api/v1/tasks/{task_id}/stream",
        status_url=f"/api/v1/tasks/{task_id}",
        queue=payload.priority,
        model=payload.model,
        session_id=session.id,
        context_policy=context_policy,
        tenant_id=tenant_id,
        trace_id=trace_id,
    )


@router.get("/{task_id}", response_model=TaskState)
async def get_agent_status(task_id: str, auth: AuthContext = Depends(get_auth_context)) -> TaskState:
    state = await task_manager.get_state(task_id)
    if state is None:
        from app.core.exceptions import ApiError

        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")
    if state.tenant_id != auth.tenant_id or state.user_id != auth.user.id:
        from app.core.exceptions import ApiError

        raise ApiError(status_code=403, code="TASK_FORBIDDEN", detail="Task does not belong to current user.")
    return state


@router.post("/{task_id}/resume", response_model=TaskState)
async def resume_agent(
    task_id: str,
    payload: AgentResumeRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> TaskState:
    from app.core.exceptions import ApiError

    state = await task_manager.get_state(task_id)
    if state is None:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")
    if state.tenant_id != auth.tenant_id or state.user_id != auth.user.id:
        raise ApiError(status_code=403, code="TASK_FORBIDDEN", detail="Task does not belong to current user.")

    run_spec = await task_manager.get_run_spec(task_id)
    if not run_spec:
        raise ApiError(status_code=409, code="TASK_RUN_SPEC_MISSING", detail="Task cannot be resumed.")

    if payload.approved_tool:
        await task_manager.approve_tool(task_id, payload.approved_tool)

    resumed = await task_manager.resume(task_id)
    if not resumed:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")

    if settings.environment == "test":
        await run_agent_task_inline(
            task_id=task_id,
            agent_type=run_spec["agent_type"],
            prompt=run_spec["prompt"],
            model=run_spec["model"],
            tenant_id=run_spec["tenant_id"],
            trace_id=run_spec["trace_id"],
            session_id=run_spec["session_id"],
            user_id=run_spec["user_id"],
            context_policy=run_spec["context_policy"],
            resume_from_step=payload.resume_from_step,
        )
    else:
        run_agent_task.apply_async(
            kwargs={
                "task_id": task_id,
                "agent_type": run_spec["agent_type"],
                "prompt": run_spec["prompt"],
                "model": run_spec["model"],
                "tenant_id": run_spec["tenant_id"],
                "trace_id": run_spec["trace_id"],
                "session_id": run_spec["session_id"],
                "user_id": run_spec["user_id"],
                "context_policy": run_spec["context_policy"],
                "resume_from_step": payload.resume_from_step,
            },
            queue=run_spec.get("priority", "default"),
            routing_key=run_spec.get("priority", "default"),
        )
    next_state = await task_manager.get_state(task_id)
    assert next_state is not None
    return next_state
