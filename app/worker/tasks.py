import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.agent.tool_bindings import resolve_agent_tool_binding
from app.agent.openai_adapter import stream_openai_agents
from app.agent.factory import create_agent
from app.agent.llm_planner import PlanBuildInput, build_plan
from app.agent.planner import PlanStep, StructuredPlan, load_plan_from_checkpoints, replan_remaining_steps
from app.agent.skill_resolver import merged_skill_permissions, resolve_runtime_tool_names
from app.services.skill_environment import resolve_specs_by_names
from app.agent.hooks import emit_hook, register_default_hooks
from app.agent.hook_registry import HookEvent
from app.agent.subagent_runner import run_subagent_step
from app.core.config import get_settings
from app.core.telemetry import telemetry
from app.schemas.realtime import AgentEvent
from app.schemas.session import AgentContextSnapshot
from app.services.context_loader import ContextLoader
from app.services.approval_flow import ApprovalFlowService
from app.services.model_router import route_model
from app.services.secret_crypto import decrypt_provider_secret
from app.services.session_history import SessionHistoryService, estimate_tokens, normalize_context_policy
from app.services.session_persistence import SessionPersistenceService
from app.services.spliceai_jobs import SpliceAIJobService
from app.services.spliceai_client import score_variant_via_service
from app.services.task_manager import task_manager
from app.db.session import SessionLocal
from app.schemas.spliceai import SpliceAIResult, SpliceAIScoreBreakdown
from app.tools import tool_registry
from app.tools.executor import ToolExecutor
from app.tools.runtime import build_openai_agent_tools
from app.tools.schemas import ToolExecutionContext
from app.worker.celery_app import celery_app

settings = get_settings()

_worker_event_loop: asyncio.AbstractEventLoop | None = None


def _build_message_metadata(
    *,
    agent_type: str,
    model: str | None,
    context_policy: str,
    skill_names: list[str],
    context_pack_ids: list[str],
    usage: dict[str, int] | None = None,
) -> dict:
    metadata = {
        "agent_type": agent_type,
        "model": model,
        "context_policy": context_policy,
        "skill_names": skill_names,
        "context_pack_ids": context_pack_ids,
    }
    if usage:
        metadata["usage"] = usage
    return metadata


def _run_async(coro):
    global _worker_event_loop
    if _worker_event_loop is None or _worker_event_loop.is_closed():
        _worker_event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_event_loop)
    return _worker_event_loop.run_until_complete(coro)


@celery_app.task(name="agent.run", bind=True)
def run_agent_task(
    self,
    task_id: str,
    agent_type: str,
    prompt: str,
    model: str | None = None,
    tenant_id: str = "public",
    trace_id: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
    context_policy: str = "balanced",
    resume_from_step: int | None = None,
    permissions: list[str] | None = None,
    scopes: list[str] | None = None,
    persist_user_message: bool = True,
    provider_base_url: str | None = None,
    provider_base_url_redacted: str | None = None,
    provider_api_key_ref: str | None = None,
    provider_api_key_ciphertext: str | None = None,
    provider_name: str | None = None,
    requested_model: str | None = None,
    fallback_models: list[str] | None = None,
) -> None:
    try:
        with telemetry.span("agent.run", task_id=task_id, agent_type=agent_type, tenant_id=tenant_id):
            _run_async(
                _run_agent_task(
                    task_id=task_id,
                    agent_type=agent_type,
                    prompt=prompt,
                    model=model,
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                    session_id=session_id,
                    user_id=user_id,
                    context_policy=context_policy,
                    resume_from_step=resume_from_step,
                    permissions=set(permissions or []),
                    scopes=set(scopes or []),
                    persist_user_message=persist_user_message,
                    provider_base_url=provider_base_url,
                    provider_base_url_redacted=provider_base_url_redacted,
                    provider_api_key_ref=provider_api_key_ref,
                    provider_api_key_ciphertext=provider_api_key_ciphertext,
                    provider_name=provider_name,
                    requested_model=requested_model,
                    fallback_models=list(fallback_models or []),
                )
            )
    except Exception as exc:
        _handle_retry_or_deadletter(
            celery_task=self,
            task_id=task_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            exception=exc,
        )


@celery_app.task(name="task.demo", bind=True)
def run_demo_task(self, task_id: str, trace_id: str | None = None) -> None:
    try:
        _run_async(_run_demo_task(task_id))
    except Exception as exc:
        _handle_retry_or_deadletter(
            celery_task=self,
            task_id=task_id,
            trace_id=trace_id,
            tenant_id="system",
            exception=exc,
        )


@celery_app.task(name="session.refresh_memory", bind=True)
def refresh_session_memory_task(self, session_id: str, trace_id: str | None = None) -> None:
    try:
        _run_async(_refresh_session_memory(session_id=session_id, trace_id=trace_id))
    except Exception as exc:
        _ = self  # reserved for future retry policy on maintenance jobs
        raise exc


@celery_app.task(name="approval.scan_overdue", bind=True)
def scan_overdue_approval_tickets_task(self) -> dict[str, int]:
    try:
        return _run_async(_scan_overdue_approval_tickets())
    except Exception as exc:
        _ = self
        raise exc


@celery_app.task(name="spliceai.run", bind=True)
def run_spliceai_job_task(
    self,
    job_id: str,
    trace_id: str | None = None,
    tenant_id: str = "public",
) -> None:
    try:
        _run_async(_run_spliceai_job(job_id=job_id, trace_id=trace_id, tenant_id=tenant_id))
    except Exception as exc:
        _ = self
        _run_async(_mark_spliceai_failed(job_id, f"{type(exc).__name__}: {exc}"))
        raise exc


def _handle_retry_or_deadletter(celery_task, task_id: str, trace_id: str | None, tenant_id: str, exception: Exception) -> None:
    retry_count = celery_task.request.retries
    max_retries = settings.celery_task_max_retries
    reason = f"{type(exception).__name__}: {exception}"

    _run_async(task_manager.increment_retry(task_id, reason))
    if retry_count < max_retries:
        backoff = settings.celery_retry_backoff_seconds * (2**retry_count)
        countdown = min(backoff, settings.celery_retry_backoff_max_seconds)
        raise celery_task.retry(exc=exception, countdown=countdown, max_retries=max_retries)

    _run_async(task_manager.mark_dead_letter(task_id, reason))
    _run_async(
        task_manager.emit(
            AgentEvent(
                id="placeholder",
                type="status",
                task_id=task_id,
                payload={
                    "status": "failed",
                    "message": "Task moved to dead letter after retries exhausted",
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "reason": reason,
                },
            )
        )
    )


async def _run_agent_task(
    task_id: str,
    agent_type: str,
    prompt: str,
    model: str | None,
    tenant_id: str = "public",
    trace_id: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
    context_policy: str = "balanced",
    resume_from_step: int | None = None,
    permissions: set[str] | None = None,
    scopes: set[str] | None = None,
    persist_user_message: bool = True,
    provider_base_url: str | None = None,
    provider_base_url_redacted: str | None = None,
    provider_api_key_ref: str | None = None,
    provider_api_key_ciphertext: str | None = None,
    provider_name: str | None = None,
    requested_model: str | None = None,
    fallback_models: list[str] | None = None,
) -> None:
    async with SessionLocal() as db:
        history = SessionHistoryService(db)
        policy = normalize_context_policy(context_policy)
        session = await history.ensure_session(session_id=session_id, tenant_id=tenant_id, user_id=user_id)
        run_usage: dict[str, int] | None = None

        user_message = None
        if persist_user_message:
            user_message = await history.add_message(
                session_id=session.id,
                role="user",
                content=prompt,
                trace_id=trace_id,
                token_estimate=estimate_tokens(prompt, model=model),
                task_id=task_id,
                metadata={"agent_type": agent_type, "context_policy": policy},
            )

        context_loader = ContextLoader(db)
        snapshot = await context_loader.load_snapshot(
            session_id=session.id,
            tenant_id=tenant_id,
            user_prompt=prompt,
            context_policy=policy,
            agent_type=agent_type,
        )
        register_default_hooks()
        emit_hook(
            HookEvent.SESSION_START,
            tenant_id=tenant_id,
            task_id=task_id,
            session_id=session.id,
            trace_id=trace_id,
            user_id=user_id,
            agent_type=agent_type,
            metadata={
                "context_pack_ids": snapshot.context_pack_ids,
                "skill_names": snapshot.skill_names,
            },
        )
        model_input = _render_model_input(snapshot)
        runtime_route = route_model(
            requested_model=requested_model or (model or settings.model_router_auto_alias),
            prompt=model_input,
            agent_type=agent_type,
            tenant_id=tenant_id,
            tools_count=len(resolve_agent_tool_binding(agent_type).tools),
        )
        effective_model = model or runtime_route.selected_model
        provider_api_key = await _resolve_provider_api_key(
            provider_api_key_ref=provider_api_key_ref,
            provider_api_key_ciphertext=provider_api_key_ciphertext,
        )
        await history.create_session_run(
            task_id=task_id,
            session_id=session.id,
            tenant_id=tenant_id,
            user_id=user_id,
            trace_id=trace_id,
            agent_type=agent_type,
            model=effective_model,
            context_policy=policy,
            turn_index=user_message.turn_index if user_message else None,
            resolved_skills=snapshot.skill_names,
            context_pack_ids=snapshot.context_pack_ids,
            routing_json={
                "requested_model": requested_model or model,
                "selected_model": effective_model,
                "complexity_score": runtime_route.complexity_score,
                "estimated_tokens": runtime_route.estimated_tokens,
                "token_limit": runtime_route.token_limit,
                "fallback_chain": runtime_route.fallback_chain,
                "reason": runtime_route.reason,
            },
        )
        await task_manager.save_checkpoint(
            task_id,
            {
                "kind": "model_routed",
                "requested_model": requested_model or model,
                "selected_model": effective_model,
                "complexity_score": runtime_route.complexity_score,
                "estimated_tokens": runtime_route.estimated_tokens,
                "token_limit": runtime_route.token_limit,
                "fallback_chain": runtime_route.fallback_chain,
                "reason": runtime_route.reason,
                "provider_name": provider_name,
                "provider_base_url": provider_base_url_redacted,
            },
        )
        binding = resolve_agent_tool_binding(agent_type)
        runtime_tool_names = resolve_runtime_tool_names(agent_type=agent_type, skill_names=snapshot.skill_names)
        effective_permissions = set(permissions or set())
        effective_permissions.update(merged_skill_permissions(resolve_specs_by_names(snapshot.skill_names)))
        candidate_specs = tool_registry.list_for_agent(runtime_tool_names)
        allowed_specs = [spec for spec in candidate_specs if tool_registry.is_allowed_for_tenant(tenant_id, spec.name)]
        disallowed_tool_names = [spec.name for spec in candidate_specs if spec.name not in {allowed.name for allowed in allowed_specs}]

        await task_manager.emit(
            AgentEvent(
                id="placeholder",
                type="status",
                task_id=task_id,
                payload={
                    "status": "running",
                    "message": "Context prepared",
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "session_id": session.id,
                    "context_budget_used": snapshot.context_budget_used,
                    "summary_version": snapshot.summary_version,
                    "input_tokens_by_layer": snapshot.input_tokens_by_layer,
                    "trimmed_items_count": snapshot.trimmed_items_count,
                    "summary_hit": snapshot.summary_hit,
                    "kv_hit": snapshot.kv_hit,
                    "agent_type": agent_type,
                    "tools_allowed": [spec.name for spec in allowed_specs],
                    "model_requested": requested_model or model,
                    "model_selected": effective_model,
                    "model_route_reason": runtime_route.reason,
                    "estimated_input_tokens": runtime_route.estimated_tokens,
                    "model_token_limit": runtime_route.token_limit,
                    "provider_name": provider_name,
                    "provider_base_url": provider_base_url_redacted,
                },
            )
        )
        for tool_name in disallowed_tool_names:
            await task_manager.emit(
                AgentEvent(
                    id="placeholder",
                    type="tool_error",
                    task_id=task_id,
                    payload={
                        "tool_name": tool_name,
                        "call_id": str(uuid4()),
                        "error_code": "TOOL_NOT_ALLOWED_FOR_TENANT",
                        "message": f"Tenant '{tenant_id}' cannot use tool '{tool_name}'",
                        "retryable": False,
                        "trace_id": trace_id,
                        "tenant_id": tenant_id,
                        "session_id": session.id,
                    },
                )
            )

        if agent_type in {"supervisor", "orchestrator"}:
            full_text, usage = await _run_supervisor_workflow(
                task_id=task_id,
                agent_type=agent_type,
                model=effective_model or "builtin",
                prompt=model_input,
                tenant_id=tenant_id,
                trace_id=trace_id,
                session_id=session.id,
                user_id=user_id,
                instructions=snapshot.instructions,
                context_budget_used=snapshot.context_budget_used,
                summary_version=snapshot.summary_version,
                tools_specs=allowed_specs,
                resume_from_step=resume_from_step,
                permissions=effective_permissions,
                scopes=scopes or set(),
                provider_base_url=provider_base_url,
                provider_base_url_redacted=provider_base_url_redacted,
                provider_api_key=provider_api_key,
                provider_name=provider_name,
                fallback_models=fallback_models or runtime_route.fallback_chain,
            )
            post_state = await task_manager.get_state(task_id)
            if post_state and post_state.awaiting_approval:
                await history.complete_session_run(task_id=task_id, status="awaiting_approval", usage_json=run_usage)
                return
            if full_text:
                run_usage = usage
                await history.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=full_text,
                    trace_id=trace_id,
                    task_id=task_id,
                    token_estimate=usage["output_tokens"] if usage and "output_tokens" in usage else estimate_tokens(full_text, model=effective_model),
                    metadata=_build_message_metadata(
                        agent_type=agent_type,
                        model=effective_model,
                        context_policy=policy,
                        skill_names=snapshot.skill_names,
                        context_pack_ids=snapshot.context_pack_ids,
                        usage=usage,
                    ),
                )
        elif effective_model and effective_model != "builtin":
            full_text, usage = await _run_openai_stream_task(
                task_id=task_id,
                model=effective_model,
                prompt=model_input,
                trace_id=trace_id,
                tenant_id=tenant_id,
                session_id=session.id,
                instructions=snapshot.instructions,
                summary_version=snapshot.summary_version,
                context_budget_used=snapshot.context_budget_used,
                tools_specs=allowed_specs,
                user_id=user_id,
                agent_type=agent_type,
                permissions=effective_permissions,
                scopes=scopes or set(),
                provider_base_url=provider_base_url,
                provider_base_url_redacted=provider_base_url_redacted,
                provider_api_key=provider_api_key,
                provider_name=provider_name,
                fallback_models=fallback_models or runtime_route.fallback_chain,
            )
            if full_text:
                run_usage = usage
                await history.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=full_text,
                    trace_id=trace_id,
                    task_id=task_id,
                    token_estimate=usage["output_tokens"] if usage and "output_tokens" in usage else estimate_tokens(full_text, model=effective_model),
                    metadata=_build_message_metadata(
                        agent_type=agent_type,
                        model=effective_model,
                        context_policy=policy,
                        skill_names=snapshot.skill_names,
                        context_pack_ids=snapshot.context_pack_ids,
                        usage=usage,
                    ),
                )
        else:
            builtin_skill = binding.skills[0] if binding.skills else agent_type
            agent = create_agent(task_id=task_id, agent_type=builtin_skill, model=effective_model)
            result = await agent.run(prompt=model_input)
            full_text = ""
            delta_chunks: list[str] = []
            for event in result.events:
                if await task_manager.cancelled(task_id):
                    await task_manager.emit(
                        AgentEvent(
                            id="placeholder",
                            type="status",
                            task_id=task_id,
                            payload={"status": "cancelled", "message": "Task interrupted", "trace_id": trace_id, "tenant_id": tenant_id},
                        )
                    )
                    return
                if event.type == "part" and event.payload.get("name") == "final_text":
                    full_text = event.payload.get("content", "")
                elif event.type == "delta":
                    chunk = str(event.payload.get("chunk", ""))
                    if chunk:
                        delta_chunks.append(chunk)
                event.payload["trace_id"] = trace_id
                event.payload["tenant_id"] = tenant_id
                event.payload["session_id"] = session.id
                event.payload["context_budget_used"] = snapshot.context_budget_used
                event.payload["summary_version"] = snapshot.summary_version
                await task_manager.emit(event)

            if not full_text and delta_chunks:
                full_text = "".join(delta_chunks).strip()
            if full_text:
                await task_manager.emit(
                    AgentEvent(
                        id=str(uuid4()),
                        type="part",
                        task_id=task_id,
                        payload={
                            "name": "final_text",
                            "content": full_text,
                            "trace_id": trace_id,
                            "tenant_id": tenant_id,
                            "session_id": session.id,
                            "context_budget_used": snapshot.context_budget_used,
                            "summary_version": snapshot.summary_version,
                        },
                    )
                )
                await task_manager.emit(
                    AgentEvent(
                        id=str(uuid4()),
                        type="usage",
                        task_id=task_id,
                        payload={
                            "input_tokens": estimate_tokens(model_input, model=effective_model),
                            "output_tokens": estimate_tokens(full_text, model=effective_model),
                            "trace_id": trace_id,
                            "tenant_id": tenant_id,
                            "session_id": session.id,
                            "context_budget_used": snapshot.context_budget_used,
                            "summary_version": snapshot.summary_version,
                        },
                    )
                )
                await task_manager.emit(
                    AgentEvent(
                        id="placeholder",
                        type="status",
                        task_id=task_id,
                        payload={
                            "status": "success",
                            "message": "Agent completed",
                            "trace_id": trace_id,
                            "tenant_id": tenant_id,
                            "session_id": session.id,
                            "context_budget_used": snapshot.context_budget_used,
                            "summary_version": snapshot.summary_version,
                        },
                    )
                )

            if full_text:
                run_usage = {
                    "input_tokens": estimate_tokens(model_input, model=effective_model),
                    "output_tokens": estimate_tokens(full_text, model=effective_model),
                }
                await history.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=full_text,
                    trace_id=trace_id,
                    task_id=task_id,
                    token_estimate=run_usage["output_tokens"],
                    metadata=_build_message_metadata(
                        agent_type=agent_type,
                        model=effective_model,
                        context_policy=policy,
                        skill_names=snapshot.skill_names,
                        context_pack_ids=snapshot.context_pack_ids,
                        usage=run_usage,
                    ),
                )

        final_state = await task_manager.get_state(task_id)
        run_status = "success"
        if final_state:
            if final_state.awaiting_approval:
                run_status = "awaiting_approval"
            elif final_state.status in {"cancelled", "failed"}:
                run_status = final_state.status
        plan_json = None
        if agent_type in {"supervisor", "orchestrator"}:
            checkpoints = await task_manager.list_checkpoints(task_id, limit=200)
            restored_plan = load_plan_from_checkpoints(checkpoints)
            if restored_plan:
                plan_json = {
                    "plan_version": restored_plan.plan_version,
                    "strategy": restored_plan.strategy,
                    "steps": [
                        {
                            "step_id": step.step_id,
                            "title": step.title,
                            "agent_role": step.agent_role,
                            "tools": step.tools,
                        }
                        for step in restored_plan.steps
                    ],
                }
        await history.complete_session_run(
            task_id=task_id,
            status=run_status,
            usage_json=run_usage,
            plan_json=plan_json,
        )

        if await history.should_refresh_summary(session.id):
            refresh_session_memory_task.apply_async(kwargs={"session_id": session.id, "trace_id": trace_id})
        await history.retention_cleanup(session.id)
        emit_hook(
            HookEvent.SESSION_STOP,
            tenant_id=tenant_id,
            task_id=task_id,
            session_id=session.id,
            trace_id=trace_id,
            user_id=user_id,
            agent_type=agent_type,
        )


async def _run_supervisor_workflow(
    task_id: str,
    agent_type: str,
    model: str,
    prompt: str,
    tenant_id: str,
    trace_id: str | None,
    session_id: str | None,
    user_id: int | None,
    instructions: str,
    context_budget_used: int | None,
    summary_version: int | None,
    tools_specs: list[Any],
    resume_from_step: int | None = None,
    permissions: set[str] | None = None,
    scopes: set[str] | None = None,
    provider_base_url: str | None = None,
    provider_base_url_redacted: str | None = None,
    provider_api_key: str | None = None,
    provider_name: str | None = None,
    fallback_models: list[str] | None = None,
) -> tuple[str, dict[str, int] | None]:
    available_tool_names = [name for name in [getattr(spec, "name", "") for spec in tools_specs] if name]
    checkpoints = await task_manager.list_checkpoints(task_id, limit=200)
    plan = load_plan_from_checkpoints(checkpoints)
    if plan is None:
        plan = await build_plan(
            PlanBuildInput(
                prompt=prompt,
                available_tools=available_tool_names,
                agent_type=agent_type,
                tenant_id=tenant_id,
                model=model,
                max_steps=5,
            )
        )
        await task_manager.save_checkpoint(
            task_id,
            {
                "kind": "plan_created",
                "plan_version": plan.plan_version,
                "strategy": plan.strategy,
                "steps": [
                    {
                        "step_id": step.step_id,
                        "title": step.title,
                        "prompt": step.prompt[:1200],
                        "depends_on": step.depends_on,
                        "tools": step.tools,
                        "agent_role": step.agent_role,
                        "success_criteria": step.success_criteria,
                    }
                    for step in plan.steps
                ],
            },
        )
        emit_hook(
            HookEvent.PLAN_CREATED,
            tenant_id=tenant_id,
            task_id=task_id,
            session_id=session_id,
            trace_id=trace_id,
            agent_type=agent_type,
            metadata={"plan_version": plan.plan_version, "step_count": len(plan.steps)},
        )
    subtasks = plan.steps
    tool_executor = ToolExecutor(tool_registry)
    approved_tools = await task_manager.approved_tools(task_id)
    step_timeout_seconds = max(
        1,
        min(settings.workflow_step_timeout_seconds_default, settings.workflow_step_timeout_seconds_max),
    )
    step_max_retries = max(0, settings.workflow_step_max_retries)
    completed_from_state = sum(1 for item in checkpoints if item.get("kind") == "step_completed")
    start_idx = resume_from_step if resume_from_step is not None else completed_from_state
    start_idx = max(0, min(start_idx, len(subtasks)))
    outputs: list[str] = [item.get("output", "") for item in checkpoints if item.get("kind") == "step_completed"]
    outputs = outputs[:start_idx]
    idx = start_idx
    replan_attempts = 0

    while idx < len(subtasks):
        if await task_manager.cancelled(task_id):
            await task_manager.emit(
                AgentEvent(
                    id="placeholder",
                    type="status",
                    task_id=task_id,
                    payload={
                        "status": "cancelled",
                        "message": "Task interrupted",
                        "trace_id": trace_id,
                        "tenant_id": tenant_id,
                        "session_id": session_id,
                        "workflow_step": idx,
                    },
                )
            )
            return "", None

        subtask = subtasks[idx]
        worker_name = subtask.agent_role or ("research_worker" if idx % 2 == 0 else "report_worker")
        step_tool_names = set(subtask.tools) if subtask.tools else set(available_tool_names)
        step_tool_specs = [spec for spec in tools_specs if getattr(spec, "name", "") in step_tool_names]
        subagent = await run_subagent_step(
            parent_task_id=task_id,
            role=worker_name,
            prompt=subtask.prompt,
            trace_id=trace_id,
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            permissions=permissions or set(),
            scopes=scopes or set(),
            approved_tools=approved_tools,
            step_tools=subtask.tools,
            executor=tool_executor,
        )
        await task_manager.save_checkpoint(
            task_id,
            {
                "kind": "step_started",
                "step": idx,
                "step_id": subtask.step_id,
                "worker": worker_name,
                "prompt": subtask.prompt[:300],
                "tools": subtask.tools,
            },
        )
        await task_manager.emit(
            AgentEvent(
                id="placeholder",
                type="checkpoint_saved",
                task_id=task_id,
                payload={"workflow_step": idx, "worker": worker_name},
            )
        )
        await task_manager.emit(
            AgentEvent(
                id="placeholder",
                type="handoff_start",
                task_id=task_id,
                payload={
                    "from_agent": agent_type,
                    "to_agent": worker_name,
                    "workflow_step": idx + 1,
                    "subtask": subtask.prompt[:500],
                    "plan_step_id": subtask.step_id,
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                },
            )
        )

        worker_output = ""
        attempt = 0
        while attempt <= step_max_retries:
            try:
                with telemetry.span(
                    "agent.workflow.step",
                    task_id=task_id,
                    step_id=subtask.step_id,
                    worker=worker_name,
                    attempt=attempt,
                    tenant_id=tenant_id,
                ):
                    if model != "builtin":
                        worker_prompt = f"[WorkerTask]\n{subtask.prompt}"
                        if subagent.output:
                            worker_prompt = f"{worker_prompt}\n\n[SubagentSummary]\n{subagent.output[:1500]}"
                        if subagent.evidence_block:
                            worker_prompt = f"{worker_prompt}\n\n{subagent.evidence_block}"
                        worker_output, _ = await asyncio.wait_for(
                            _run_openai_stream_task(
                                task_id=task_id,
                                model=model,
                                prompt=worker_prompt,
                                trace_id=trace_id,
                                tenant_id=tenant_id,
                                session_id=session_id,
                                instructions=f"{instructions}\n\nYou are {worker_name}. Focus only on current subtask.",
                                summary_version=summary_version,
                                context_budget_used=context_budget_used,
                                tools_specs=step_tool_specs,
                                user_id=user_id,
                                agent_type=worker_name,
                                suppress_terminal_events=True,
                                permissions=permissions or set(),
                                scopes=scopes or set(),
                                provider_base_url=provider_base_url,
                            provider_base_url_redacted=provider_base_url_redacted,
                                provider_api_key=provider_api_key,
                                provider_name=provider_name,
                                fallback_models=fallback_models or [],
                            ),
                            timeout=step_timeout_seconds,
                        )
                    else:
                        async def _run_builtin_worker() -> str:
                            local_output = ""
                            agent = create_agent(task_id=task_id, agent_type="echo", model="builtin")
                            result = await agent.run(prompt=f"[{worker_name}] {subtask.prompt}")
                            for event in result.events:
                                event.payload["workflow_step"] = idx + 1
                                event.payload["trace_id"] = trace_id
                                event.payload["tenant_id"] = tenant_id
                                event.payload["session_id"] = session_id
                                await task_manager.emit(event)
                                if event.type == "part" and event.payload.get("name") == "final_text":
                                    local_output = str(event.payload.get("content", ""))
                            return local_output

                        worker_output = await asyncio.wait_for(_run_builtin_worker(), timeout=step_timeout_seconds)
                post_state = await task_manager.get_state(task_id)
                if post_state and post_state.awaiting_approval:
                    return "", None
                break
            except asyncio.TimeoutError:
                await task_manager.save_checkpoint(
                    task_id,
                    {
                        "kind": "step_timeout",
                        "step": idx,
                        "step_id": subtask.step_id,
                        "worker": worker_name,
                        "attempt": attempt,
                        "timeout_seconds": step_timeout_seconds,
                    },
                )
                await task_manager.emit(
                    AgentEvent(
                        id="placeholder",
                        type="step_timeout",
                        task_id=task_id,
                        payload={
                            "workflow_step": idx + 1,
                            "worker": worker_name,
                            "plan_step_id": subtask.step_id,
                            "retry_count": attempt,
                            "max_retries": step_max_retries,
                            "timeout_seconds": step_timeout_seconds,
                            "trace_id": trace_id,
                            "tenant_id": tenant_id,
                            "session_id": session_id,
                        },
                    )
                )
                if attempt >= step_max_retries:
                    await task_manager.emit(
                        AgentEvent(
                            id="placeholder",
                            type="status",
                            task_id=task_id,
                            payload={
                                "status": "failed",
                                "message": f"Step timeout after retries: {worker_name}",
                                "workflow_step": idx + 1,
                                "plan_step_id": subtask.step_id,
                                "trace_id": trace_id,
                                "tenant_id": tenant_id,
                                "session_id": session_id,
                            },
                        )
                    )
                    completed_ids = {step.step_id for step in subtasks[:idx]}
                    replanned = replan_remaining_steps(
                        plan=plan,
                        completed_step_ids=completed_ids,
                        failure_step_id=subtask.step_id,
                        failure_reason=f"timeout:{worker_name}",
                    )
                    await task_manager.save_checkpoint(
                        task_id,
                        {
                            "kind": "plan_recomputed",
                            "plan_version": replanned.plan_version,
                            "strategy": replanned.strategy,
                            "failure_step_id": subtask.step_id,
                            "steps": [
                                {
                                    "step_id": step.step_id,
                                    "title": step.title,
                                    "prompt": step.prompt[:1200],
                                    "depends_on": step.depends_on,
                                    "tools": step.tools,
                                    "agent_role": step.agent_role,
                                    "success_criteria": step.success_criteria,
                                }
                                for step in replanned.steps
                            ],
                        },
                    )
                    if replanned.steps and replan_attempts < settings.workflow_max_replan_attempts:
                        plan = replanned
                        subtasks = replanned.steps
                        replan_attempts += 1
                        idx = 0
                        continue
                    return "", None
                attempt += 1
                await asyncio.sleep(settings.workflow_step_retry_backoff_seconds * attempt)
            except Exception as exc:  # noqa: BLE001
                if attempt >= step_max_retries:
                    await task_manager.emit(
                        AgentEvent(
                            id="placeholder",
                            type="status",
                            task_id=task_id,
                            payload={
                                "status": "failed",
                                "message": f"Step failed: {worker_name}: {type(exc).__name__}",
                                "workflow_step": idx + 1,
                                "plan_step_id": subtask.step_id,
                                "trace_id": trace_id,
                                "tenant_id": tenant_id,
                                "session_id": session_id,
                            },
                        )
                    )
                    completed_ids = {step.step_id for step in subtasks[:idx]}
                    replanned = replan_remaining_steps(
                        plan=plan,
                        completed_step_ids=completed_ids,
                        failure_step_id=subtask.step_id,
                        failure_reason=f"{type(exc).__name__}: {exc}",
                    )
                    await task_manager.save_checkpoint(
                        task_id,
                        {
                            "kind": "plan_recomputed",
                            "plan_version": replanned.plan_version,
                            "strategy": replanned.strategy,
                            "failure_step_id": subtask.step_id,
                            "steps": [
                                {
                                    "step_id": step.step_id,
                                    "title": step.title,
                                    "prompt": step.prompt[:1200],
                                    "depends_on": step.depends_on,
                                    "tools": step.tools,
                                    "agent_role": step.agent_role,
                                    "success_criteria": step.success_criteria,
                                }
                                for step in replanned.steps
                            ],
                        },
                    )
                    if replanned.steps and replan_attempts < settings.workflow_max_replan_attempts:
                        plan = replanned
                        subtasks = replanned.steps
                        replan_attempts += 1
                        idx = 0
                        continue
                    return "", None
                attempt += 1
                await asyncio.sleep(settings.workflow_step_retry_backoff_seconds * attempt)

        outputs.append(worker_output)
        await task_manager.save_checkpoint(
            task_id,
            {
                "kind": "step_completed",
                "step": idx,
                "step_id": subtask.step_id,
                "worker": worker_name,
                "output_preview": worker_output[:300],
                "output": worker_output[:2000],
            },
        )
        await task_manager.emit(
            AgentEvent(
                id="placeholder",
                type="checkpoint_saved",
                task_id=task_id,
                payload={"workflow_step": idx + 1, "worker": worker_name},
            )
        )
        await task_manager.emit(
            AgentEvent(
                id="placeholder",
                type="handoff_end",
                task_id=task_id,
                payload={
                    "from_agent": worker_name,
                    "to_agent": agent_type,
                    "workflow_step": idx + 1,
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                },
            )
        )

        idx += 1

    final_text = "\n\n".join([item for item in outputs if item]).strip()
    await task_manager.emit(
        AgentEvent(
            id=str(uuid4()),
            type="part",
            task_id=task_id,
            payload={
                "name": "final_text",
                "content": final_text,
                "trace_id": trace_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
                "workflow_step": len(subtasks),
            },
        )
    )
    await task_manager.emit(
        AgentEvent(
            id="placeholder",
            type="status",
            task_id=task_id,
            payload={
                "status": "success",
                "message": "Supervisor workflow completed",
                "trace_id": trace_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
                "workflow_step": len(subtasks),
                "awaiting_approval": False,
            },
        )
    )
    return final_text, {"output_tokens": estimate_tokens(final_text), "input_tokens": estimate_tokens(prompt)}


async def _run_openai_stream_task(
    task_id: str,
    model: str,
    prompt: str,
    trace_id: str | None = None,
    tenant_id: str = "public",
    session_id: str | None = None,
    instructions: str | None = None,
    summary_version: int | None = None,
    context_budget_used: int | None = None,
    tools_specs: list[Any] | None = None,
    user_id: int | None = None,
    agent_type: str | None = None,
    suppress_terminal_events: bool = False,
    permissions: set[str] | None = None,
    scopes: set[str] | None = None,
    provider_base_url: str | None = None,
    provider_base_url_redacted: str | None = None,
    provider_api_key: str | None = None,
    provider_name: str | None = None,
    fallback_models: list[str] | None = None,
) -> tuple[str, dict[str, int] | None]:
    await task_manager.emit(
        AgentEvent(
            id="placeholder",
            type="status",
            task_id=task_id,
            created_at=datetime.now(timezone.utc),
            payload={
                "status": "running",
                "message": "OpenAI agent streaming started",
                "model": model,
                "trace_id": trace_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
                "context_budget_used": context_budget_used,
                "summary_version": summary_version,
                "agent_type": agent_type,
                "tools_allowed": [getattr(spec, "name", str(spec)) for spec in (tools_specs or [])],
                "provider_name": provider_name,
                "provider_base_url": provider_base_url_redacted,
            },
        )
    )

    final_instructions = instructions or (
        "You are a backend report generation agent. "
        "Return concise, accurate report text in plain markdown without code fences."
    )

    async def on_delta(chunk: str) -> None:
        await task_manager.emit(
            AgentEvent(
                id="placeholder",
                type="delta",
                task_id=task_id,
                payload={
                    "chunk": chunk,
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "context_budget_used": context_budget_used,
                    "summary_version": summary_version,
                },
            )
        )

    async def on_tool_event(event_type: str, payload: dict[str, Any]) -> None:
        enriched_payload = dict(payload)
        if event_type == "approval_required":
            async with SessionLocal() as approval_db:
                approval_service = ApprovalFlowService(approval_db)
                state = await task_manager.get_state(task_id)
                ticket = await approval_service.create_ticket(
                    task_id=task_id,
                    tenant_id=tenant_id,
                    tool_name=str(payload.get("tool_name", "unknown_tool")),
                    requested_by=user_id,
                    reviewer_id=state.reviewer_id if state else None,
                    session_id=session_id,
                    trace_id=trace_id,
                    reason=str(payload.get("message", ""))[:1000] or None,
                    sla_seconds=state.sla_seconds if state else None,
                )
                enriched_payload["approval_ticket_id"] = ticket.id
        await task_manager.emit(
            AgentEvent(
                id="placeholder",
                type=event_type,
                task_id=task_id,
                payload={
                    **enriched_payload,
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "context_budget_used": context_budget_used,
                    "summary_version": summary_version,
                    "agent_type": agent_type,
                },
            )
        )

    effective_permissions = set(permissions or {"session:read"})
    if "session:read" not in effective_permissions:
        effective_permissions.add("session:read")
    tool_context = ToolExecutionContext(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        trace_id=trace_id,
        task_id=task_id,
        permissions=effective_permissions,
        approved_tools=await task_manager.approved_tools(task_id),
        scopes=set(scopes or set()),
    )
    tools_executor = ToolExecutor(tool_registry)
    openai_tools = build_openai_agent_tools(
        specs=tools_specs or [],
        executor=tools_executor,
        context=tool_context,
        on_tool_event=on_tool_event,
    )

    effective_fallback = list(fallback_models or [])
    if provider_base_url:
        # Custom OpenAI-compatible endpoints only accept their own model names.
        effective_fallback = []

    seen_candidates: set[str] = set()
    candidates: list[str] = []
    for candidate in [model, *effective_fallback]:
        if candidate and candidate not in seen_candidates:
            seen_candidates.add(candidate)
            candidates.append(candidate)
    full_text = ""
    usage: dict[str, int] | None = None
    last_error: Exception | None = None
    for idx, candidate_model in enumerate(candidates):
        try:
            if idx > 0:
                await task_manager.emit(
                    AgentEvent(
                        id="placeholder",
                        type="status",
                        task_id=task_id,
                        payload={
                            "status": "running",
                            "message": f"Retrying with fallback model: {candidate_model}",
                            "trace_id": trace_id,
                            "tenant_id": tenant_id,
                            "session_id": session_id,
                            "fallback_attempt": idx,
                            "fallback_from": candidates[idx - 1],
                            "fallback_to": candidate_model,
                        },
                    )
                )
            with telemetry.span("agent.openai_stream", task_id=task_id, model=candidate_model, tenant_id=tenant_id):
                full_text, usage = await stream_openai_agents(
                    prompt=prompt,
                    model=candidate_model,
                    instructions=final_instructions,
                    on_delta=on_delta,
                    should_stop=lambda: task_manager.cancelled(task_id),
                    tools=openai_tools,
                    api_key=provider_api_key,
                    base_url=provider_base_url,
                )
            model = candidate_model
            break
        except RuntimeError as exc:
            last_error = exc
            if "requires approval" in str(exc).lower():
                await task_manager.emit(
                    AgentEvent(
                        id="placeholder",
                        type="status",
                        task_id=task_id,
                        payload={
                            "status": "running",
                            "message": str(exc),
                            "trace_id": trace_id,
                            "tenant_id": tenant_id,
                            "session_id": session_id,
                            "awaiting_approval": True,
                        },
                    )
                )
                return "", None
            if idx == len(candidates) - 1:
                raise
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if idx == len(candidates) - 1:
                raise
            continue

    if last_error is not None and not full_text:
        raise last_error

    if await task_manager.cancelled(task_id):
        await task_manager.emit(
            AgentEvent(
                id="placeholder",
                type="status",
                task_id=task_id,
                payload={
                    "status": "cancelled",
                    "message": "Task interrupted",
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "context_budget_used": context_budget_used,
                    "summary_version": summary_version,
                },
            )
        )
        return "", usage

    if not suppress_terminal_events:
        await task_manager.emit(
            AgentEvent(
                id=str(uuid4()),
                type="part",
                task_id=task_id,
                payload={
                    "name": "final_text",
                    "content": full_text,
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "context_budget_used": context_budget_used,
                    "summary_version": summary_version,
                },
            )
        )
        if usage:
            await task_manager.emit(
                AgentEvent(
                    id=str(uuid4()),
                    type="usage",
                    task_id=task_id,
                    payload={
                        **usage,
                        "trace_id": trace_id,
                        "tenant_id": tenant_id,
                        "session_id": session_id,
                        "context_budget_used": context_budget_used,
                        "summary_version": summary_version,
                    },
                )
            )
        await task_manager.emit(
            AgentEvent(
                id="placeholder",
                type="status",
                task_id=task_id,
                payload={
                    "status": "success",
                    "message": "Agent completed",
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "context_budget_used": context_budget_used,
                    "summary_version": summary_version,
                },
            )
        )
    return full_text, usage


async def _run_demo_task(task_id: str) -> None:
    await task_manager.emit(
        AgentEvent(id="placeholder", type="status", task_id=task_id, payload={"status": "running", "message": "task started"})
    )
    for i in range(1, 5):
        if await task_manager.cancelled(task_id):
            await task_manager.emit(
                AgentEvent(id="placeholder", type="status", task_id=task_id, payload={"status": "cancelled", "message": "cancelled by user"})
            )
            return
        await asyncio.sleep(0.5)
        await task_manager.emit(AgentEvent(id="placeholder", type="part", task_id=task_id, payload={"step": i}))
    await task_manager.emit(
        AgentEvent(id="placeholder", type="status", task_id=task_id, payload={"status": "success", "message": "task finished"})
    )


async def run_agent_task_inline(
    task_id: str,
    agent_type: str,
    prompt: str,
    model: str | None = None,
    tenant_id: str = "public",
    trace_id: str | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
    context_policy: str = "balanced",
    resume_from_step: int | None = None,
    permissions: set[str] | None = None,
    scopes: set[str] | None = None,
    persist_user_message: bool = True,
    provider_base_url: str | None = None,
    provider_base_url_redacted: str | None = None,
    provider_api_key_ref: str | None = None,
    provider_api_key_ciphertext: str | None = None,
    provider_name: str | None = None,
    requested_model: str | None = None,
    fallback_models: list[str] | None = None,
) -> None:
    await _run_agent_task(
        task_id=task_id,
        agent_type=agent_type,
        prompt=prompt,
        model=model,
        tenant_id=tenant_id,
        trace_id=trace_id,
        session_id=session_id,
        user_id=user_id,
        context_policy=context_policy,
        resume_from_step=resume_from_step,
        permissions=permissions,
        scopes=scopes,
        persist_user_message=persist_user_message,
        provider_base_url=provider_base_url,
        provider_base_url_redacted=provider_base_url_redacted,
        provider_api_key_ref=provider_api_key_ref,
        provider_api_key_ciphertext=provider_api_key_ciphertext,
        provider_name=provider_name,
        requested_model=requested_model,
        fallback_models=fallback_models,
    )


async def run_demo_task_inline(task_id: str) -> None:
    await _run_demo_task(task_id=task_id)


async def run_scan_overdue_approval_tickets_inline() -> dict[str, int]:
    return await _scan_overdue_approval_tickets()


async def run_spliceai_job_inline(job_id: str, trace_id: str | None = None, tenant_id: str = "public") -> None:
    await _run_spliceai_job(job_id=job_id, trace_id=trace_id, tenant_id=tenant_id)


async def _refresh_session_memory(session_id: str, trace_id: str | None) -> None:
    async with SessionLocal() as db:
        history = SessionHistoryService(db)
        session = await history.get_session(session_id)
        if session is None:
            return

        if await history.should_refresh_summary(session_id):
            summary_text, covered_until = await history.build_summary_text(session_id)
            summary = await history.add_summary(
                session_id=session_id,
                summary_text=summary_text,
                covered_until_turn=covered_until,
                trace_id=trace_id,
            )
        else:
            summary = await history.get_latest_summary(session_id)
            covered_until = summary.covered_until_turn if summary else 0

        memory_entries = await history.extract_memory_from_messages(session_id, since_turn=max(0, covered_until - 20))
        if memory_entries:
            await history.upsert_memory_entries(
                session_id=session_id,
                source_turn=covered_until,
                entries=memory_entries,
            )
        await history.retention_cleanup(session_id)


async def _scan_overdue_approval_tickets() -> dict[str, int]:
    async with SessionLocal() as db:
        service = ApprovalFlowService(db)
        scanned, overdue = await service.scan_overdue()
        return {"scanned": scanned, "overdue_marked": overdue}


async def _resolve_provider_api_key(
    *,
    provider_api_key_ref: str | None,
    provider_api_key_ciphertext: str | None,
) -> str | None:
    if provider_api_key_ref:
        value = await task_manager.consume_secret(provider_api_key_ref)
        if value:
            return value
    decrypted = decrypt_provider_secret(provider_api_key_ciphertext)
    if decrypted:
        return decrypted
    fallback = get_settings().default_provider_api_key.strip()
    return fallback or None


async def _run_spliceai_job(job_id: str, trace_id: str | None, tenant_id: str) -> None:
    async with SessionLocal() as db:
        service = SpliceAIJobService(db)
        persistence = SessionPersistenceService(db)
        job = await service.get_job(job_id, tenant_id=tenant_id)
        if job is None:
            return
        await service.mark_running(job_id)
        try:
            if settings.environment == "test" or not settings.spliceai_service_url:
                result = _simulate_spliceai_result(
                    variant_hgvs=job.variant_hgvs,
                    genome_build=job.genome_build,
                    gene_symbol=job.gene_symbol,
                    trace_id=trace_id,
                )
            else:
                result = await score_variant_via_service(
                    variant_hgvs=job.variant_hgvs,
                    genome_build=job.genome_build,
                    gene_symbol=job.gene_symbol,
                )
            updated = await service.mark_success(job_id, result)
            if updated is not None:
                await persistence.sync_spliceai_job_completion(job=updated, result=result)
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            updated = await service.mark_failed(job_id, message)
            if updated is not None:
                await persistence.sync_spliceai_job_completion(job=updated, error_message=message)


async def _mark_spliceai_failed(job_id: str, message: str) -> None:
    async with SessionLocal() as db:
        service = SpliceAIJobService(db)
        persistence = SessionPersistenceService(db)
        updated = await service.mark_failed(job_id, message)
        if updated is not None:
            await persistence.sync_spliceai_job_completion(job=updated, error_message=message)


def _simulate_spliceai_result(
    *,
    variant_hgvs: str,
    genome_build: str,
    gene_symbol: str | None,
    trace_id: str | None,
) -> SpliceAIResult:
    seed = hashlib.sha256(f"{variant_hgvs}|{genome_build}|{gene_symbol}|{trace_id}".encode("utf-8")).digest()
    values = [round(seed[idx] / 255.0, 4) for idx in range(4)]
    score = SpliceAIScoreBreakdown(
        ds_ag=values[0],
        ds_al=values[1],
        ds_dg=values[2],
        ds_dl=values[3],
        max_score=max(values),
    )
    if score.max_score >= 0.8:
        impact = "high"
        interpretation = "Predicted strong splice impact. Recommend downstream validation."
    elif score.max_score >= 0.5:
        impact = "moderate"
        interpretation = "Predicted moderate splice impact. Correlate with phenotype and transcript evidence."
    else:
        impact = "low"
        interpretation = "Predicted limited splice impact under current model assumptions."
    return SpliceAIResult(
        variant_hgvs=variant_hgvs,
        genome_build=genome_build,
        gene_symbol=gene_symbol,
        model_version="spliceai-mock-v1",
        score_breakdown=score,
        predicted_impact=impact,
        interpretation=interpretation,
        source="spliceai-simulated-worker",
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


def _render_model_input(snapshot: AgentContextSnapshot) -> str:
    if snapshot.context_blocks:
        return f"{snapshot.instructions}\n\n" + "\n\n".join(snapshot.context_blocks) + f"\n\n[CurrentUserMessage]\n{snapshot.input_message}"
    return f"{snapshot.instructions}\n\n[CurrentUserMessage]\n{snapshot.input_message}"
