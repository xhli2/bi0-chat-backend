from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context
from app.agent.skills import list_skill_specs, list_skills
from app.db.session import get_db_session
from app.schemas.realtime import TaskPriority, TaskState
from app.schemas.session import ContextPolicy
from app.services.model_router import route_model
from app.services.model_policy import validate_model_for_tenant
from app.services.provider_policy import validate_provider_name
from app.services.secret_crypto import decrypt_provider_secret, encrypt_provider_secret
from app.services.session_history import SessionHistoryService, normalize_context_policy
from app.services.task_manager import task_manager
from app.services.url_redaction import redact_provider_base_url
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
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    provider_name: str | None = None


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
    provider_api_key: str | None = None


class AgentSkillSpecOut(BaseModel):
    name: str
    description: str
    triggers: list[str]
    tools: list[str]
    subagent_role: str
    context_pack_ids: list[str] = Field(default_factory=list)
    bundled_scripts: list[str] = Field(default_factory=list)
    default_script: str | None = None


@router.get("/skills", response_model=list[str])
async def get_supported_skills(_: AuthContext = Depends(get_auth_context)) -> list[str]:
    return list_skills()


@router.get("/skill-specs", response_model=list[AgentSkillSpecOut])
async def get_skill_specs(_: AuthContext = Depends(get_auth_context)) -> list[AgentSkillSpecOut]:
    return [
        AgentSkillSpecOut(
            name=spec.name,
            description=spec.description,
            triggers=list(spec.triggers),
            tools=list(spec.tools),
            subagent_role=spec.subagent_role,
            context_pack_ids=list(spec.context_pack_ids),
            bundled_scripts=list(spec.bundled_scripts),
            default_script=spec.default_script,
        )
        for spec in list_skill_specs()
    ]


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

    requested_model = payload.model
    if requested_model == "builtin" and settings.default_llm_model:
        requested_model = settings.default_llm_model

    has_request_provider = bool(payload.provider_base_url or payload.provider_api_key or payload.provider_name)
    if has_request_provider:
        provider_base_url = payload.provider_base_url
        provider_api_key = payload.provider_api_key
        provider_name_input = payload.provider_name
    else:
        provider_base_url = settings.default_provider_base_url or None
        provider_api_key = settings.default_provider_api_key or None
        provider_name_input = settings.default_provider_name or None

    validate_model_for_tenant(model=requested_model, tenant_id=tenant_id)
    preflight_route = route_model(
        requested_model=requested_model,
        prompt=payload.prompt,
        agent_type=payload.agent_type,
        tenant_id=tenant_id,
    )
    selected_model = preflight_route.selected_model
    validate_model_for_tenant(model=selected_model, tenant_id=tenant_id)
    uses_custom_provider = bool(provider_base_url or provider_api_key)
    provider_name = validate_provider_name(
        name=provider_name_input,
        tenant_id=tenant_id,
        requires_name=uses_custom_provider,
    )

    history = SessionHistoryService(db)
    session = await history.ensure_session(payload.session_id, tenant_id=tenant_id, user_id=user_id)
    provider_api_key_ref = (
        await task_manager.put_secret(provider_api_key, settings.provider_secret_ttl_seconds)
        if provider_api_key
        else None
    )
    provider_api_key_ciphertext = encrypt_provider_secret(provider_api_key) if provider_api_key else None
    provider_base_url_redacted = redact_provider_base_url(provider_base_url)

    task_id = await task_manager.create_task(
        priority=payload.priority,
        model=selected_model,
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
            "model": selected_model,
            "selected_model": selected_model,
            "requested_model": requested_model,
            "tenant_id": tenant_id,
            "trace_id": trace_id,
            "session_id": session.id,
            "user_id": user_id,
            "context_policy": context_policy,
            "priority": payload.priority,
            "permissions": sorted(auth.permissions),
            "scopes": sorted(auth.scopes),
            "provider_base_url": provider_base_url,
            "provider_base_url_redacted": provider_base_url_redacted,
            "provider_name": provider_name,
            "fallback_models": preflight_route.fallback_chain,
            "provider_api_key_ref": provider_api_key_ref,
            "provider_api_key_ciphertext": provider_api_key_ciphertext,
            "model_routing": {
                "requested_model": requested_model,
                "selected_model": selected_model,
                "complexity_score": preflight_route.complexity_score,
                "estimated_tokens": preflight_route.estimated_tokens,
                "token_limit": preflight_route.token_limit,
                "reason": preflight_route.reason,
                "fallback_chain": preflight_route.fallback_chain,
            },
        },
    )
    if settings.environment == "test":
        await run_agent_task_inline(
            task_id=task_id,
            agent_type=payload.agent_type,
            prompt=payload.prompt,
            model=selected_model,
            tenant_id=tenant_id,
            trace_id=trace_id,
            session_id=session.id,
            user_id=user_id,
            context_policy=context_policy,
            permissions=set(auth.permissions),
            scopes=set(auth.scopes),
            persist_user_message=True,
            provider_base_url=provider_base_url,
            provider_base_url_redacted=provider_base_url_redacted,
            provider_api_key_ref=provider_api_key_ref,
            provider_api_key_ciphertext=provider_api_key_ciphertext,
            provider_name=provider_name,
            requested_model=requested_model,
            fallback_models=preflight_route.fallback_chain,
        )
    else:
        run_agent_task.apply_async(
            kwargs={
                "task_id": task_id,
                "agent_type": payload.agent_type,
                "prompt": payload.prompt,
                "model": selected_model,
                "tenant_id": tenant_id,
                "trace_id": trace_id,
                "session_id": session.id,
                "user_id": user_id,
                "context_policy": context_policy,
                "permissions": sorted(auth.permissions),
                "scopes": sorted(auth.scopes),
                "persist_user_message": True,
                "provider_base_url": provider_base_url,
                "provider_base_url_redacted": provider_base_url_redacted,
                "provider_api_key_ref": provider_api_key_ref,
                "provider_api_key_ciphertext": provider_api_key_ciphertext,
                "provider_name": provider_name,
                "requested_model": requested_model,
                "fallback_models": preflight_route.fallback_chain,
            },
            queue=payload.priority,
            routing_key=payload.priority,
        )
    return AgentRunResponse(
        task_id=task_id,
        stream_url=f"/api/v1/tasks/{task_id}/stream",
        status_url=f"/api/v1/tasks/{task_id}",
        queue=payload.priority,
        model=selected_model,
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
    if state.status in {"success", "failed", "cancelled"} and not state.awaiting_approval:
        raise ApiError(status_code=409, code="TASK_NOT_RESUMABLE", detail="Task is already terminal.")

    run_spec = await task_manager.get_run_spec(task_id)
    if not run_spec:
        raise ApiError(status_code=409, code="TASK_RUN_SPEC_MISSING", detail="Task cannot be resumed.")

    if payload.approved_tool:
        await task_manager.approve_tool(task_id, payload.approved_tool)

    refreshed_spec = run_spec
    if payload.provider_api_key:
        refreshed_spec = await task_manager.patch_run_spec(
            task_id,
            {
                "provider_api_key_ref": await task_manager.put_secret(
                    payload.provider_api_key, settings.provider_secret_ttl_seconds
                ),
                "provider_api_key_ciphertext": encrypt_provider_secret(payload.provider_api_key),
            },
        ) or run_spec
    elif run_spec.get("provider_api_key_ref"):
        has_secret = await task_manager.secret_exists(str(run_spec["provider_api_key_ref"]))
        if not has_secret:
            restored = decrypt_provider_secret(run_spec.get("provider_api_key_ciphertext"))
            if restored:
                refreshed_spec = await task_manager.patch_run_spec(
                    task_id,
                    {
                        "provider_api_key_ref": await task_manager.put_secret(
                            restored, settings.provider_secret_ttl_seconds
                        )
                    },
                ) or run_spec
            else:
                raise ApiError(
                    status_code=409,
                    code="PROVIDER_SECRET_EXPIRED",
                    detail="Provider API key reference expired. Please provide provider_api_key when resuming.",
                )

    resumed = await task_manager.resume(task_id)
    if not resumed:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found.")

    if settings.environment == "test":
        await run_agent_task_inline(
            task_id=task_id,
            agent_type=refreshed_spec["agent_type"],
            prompt=refreshed_spec["prompt"],
            model=refreshed_spec["model"],
            tenant_id=refreshed_spec["tenant_id"],
            trace_id=refreshed_spec["trace_id"],
            session_id=refreshed_spec["session_id"],
            user_id=refreshed_spec["user_id"],
            context_policy=refreshed_spec["context_policy"],
            resume_from_step=payload.resume_from_step,
            permissions=set(refreshed_spec.get("permissions", [])),
            scopes=set(refreshed_spec.get("scopes", [])),
            persist_user_message=False,
            provider_base_url=refreshed_spec.get("provider_base_url"),
            provider_base_url_redacted=refreshed_spec.get("provider_base_url_redacted"),
            provider_api_key_ref=refreshed_spec.get("provider_api_key_ref"),
            provider_api_key_ciphertext=refreshed_spec.get("provider_api_key_ciphertext"),
            provider_name=refreshed_spec.get("provider_name"),
            requested_model=refreshed_spec.get("requested_model", refreshed_spec["model"]),
            fallback_models=refreshed_spec.get("fallback_models", []),
        )
    else:
        run_agent_task.apply_async(
            kwargs={
                "task_id": task_id,
                "agent_type": refreshed_spec["agent_type"],
                "prompt": refreshed_spec["prompt"],
                "model": refreshed_spec["model"],
                "tenant_id": refreshed_spec["tenant_id"],
                "trace_id": refreshed_spec["trace_id"],
                "session_id": refreshed_spec["session_id"],
                "user_id": refreshed_spec["user_id"],
                "context_policy": refreshed_spec["context_policy"],
                "resume_from_step": payload.resume_from_step,
                "permissions": refreshed_spec.get("permissions", []),
                "scopes": refreshed_spec.get("scopes", []),
                "persist_user_message": False,
                "provider_base_url": refreshed_spec.get("provider_base_url"),
                "provider_base_url_redacted": refreshed_spec.get("provider_base_url_redacted"),
                "provider_api_key_ref": refreshed_spec.get("provider_api_key_ref"),
                "provider_api_key_ciphertext": refreshed_spec.get("provider_api_key_ciphertext"),
                "provider_name": refreshed_spec.get("provider_name"),
                "requested_model": refreshed_spec.get("requested_model", refreshed_spec["model"]),
                "fallback_models": refreshed_spec.get("fallback_models", []),
            },
            queue=refreshed_spec.get("priority", "default"),
            routing_key=refreshed_spec.get("priority", "default"),
        )
    next_state = await task_manager.get_state(task_id)
    if next_state is None:
        raise ApiError(status_code=404, code="TASK_NOT_FOUND", detail="Task not found after resume.")
    return next_state
