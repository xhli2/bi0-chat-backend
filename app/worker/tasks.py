import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.agent.tool_bindings import resolve_agent_tool_binding
from app.agent.openai_adapter import stream_openai_agents
from app.agent.factory import create_agent
from app.core.config import get_settings
from app.core.telemetry import telemetry
from app.schemas.realtime import AgentEvent
from app.schemas.session import AgentContextSnapshot
from app.services.context_loader import ContextLoader
from app.services.approval_flow import ApprovalFlowService
from app.services.session_history import SessionHistoryService, estimate_tokens, normalize_context_policy
from app.services.task_manager import task_manager
from app.db.session import SessionLocal
from app.tools import tool_registry
from app.tools.executor import ToolExecutor
from app.tools.runtime import build_openai_agent_tools
from app.tools.schemas import ToolExecutionContext
from app.worker.celery_app import celery_app

settings = get_settings()


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
) -> None:
    try:
        with telemetry.span("agent.run", task_id=task_id, agent_type=agent_type, tenant_id=tenant_id):
            asyncio.run(
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
        asyncio.run(_run_demo_task(task_id))
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
        asyncio.run(_refresh_session_memory(session_id=session_id, trace_id=trace_id))
    except Exception as exc:
        _ = self  # reserved for future retry policy on maintenance jobs
        raise exc


@celery_app.task(name="approval.scan_overdue", bind=True)
def scan_overdue_approval_tickets_task(self) -> dict[str, int]:
    try:
        return asyncio.run(_scan_overdue_approval_tickets())
    except Exception as exc:
        _ = self
        raise exc


def _handle_retry_or_deadletter(celery_task, task_id: str, trace_id: str | None, tenant_id: str, exception: Exception) -> None:
    retry_count = celery_task.request.retries
    max_retries = settings.celery_task_max_retries
    reason = f"{type(exception).__name__}: {exception}"

    asyncio.run(task_manager.increment_retry(task_id, reason))
    if retry_count < max_retries:
        backoff = settings.celery_retry_backoff_seconds * (2**retry_count)
        countdown = min(backoff, settings.celery_retry_backoff_max_seconds)
        raise celery_task.retry(exc=exception, countdown=countdown, max_retries=max_retries)

    asyncio.run(task_manager.mark_dead_letter(task_id, reason))
    asyncio.run(
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
) -> None:
    async with SessionLocal() as db:
        history = SessionHistoryService(db)
        policy = normalize_context_policy(context_policy)
        session = await history.ensure_session(session_id=session_id, tenant_id=tenant_id, user_id=user_id)

        await history.add_message(
            session_id=session.id,
            role="user",
            content=prompt,
            trace_id=trace_id,
            token_estimate=estimate_tokens(prompt),
        )

        context_loader = ContextLoader(db)
        snapshot = await context_loader.load_snapshot(
            session_id=session.id,
            tenant_id=tenant_id,
            user_prompt=prompt,
            context_policy=policy,
        )
        model_input = _render_model_input(snapshot)
        binding = resolve_agent_tool_binding(agent_type)
        candidate_specs = tool_registry.list_for_agent(binding.tools)
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
                model=model or "builtin",
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
            )
            if full_text:
                await history.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=full_text,
                    trace_id=trace_id,
                    token_estimate=usage["output_tokens"] if usage and "output_tokens" in usage else estimate_tokens(full_text),
                )
        elif model and model != "builtin":
            full_text, usage = await _run_openai_stream_task(
                task_id=task_id,
                model=model,
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
            )
            if full_text:
                await history.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=full_text,
                    trace_id=trace_id,
                    token_estimate=usage["output_tokens"] if usage and "output_tokens" in usage else estimate_tokens(full_text),
                )
        else:
            builtin_skill = binding.skills[0] if binding.skills else agent_type
            agent = create_agent(task_id=task_id, agent_type=builtin_skill, model=model)
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
                            "input_tokens": estimate_tokens(model_input),
                            "output_tokens": estimate_tokens(full_text),
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
                await history.add_message(
                    session_id=session.id,
                    role="assistant",
                    content=full_text,
                    trace_id=trace_id,
                    token_estimate=estimate_tokens(full_text),
                )

        if await history.should_refresh_summary(session.id):
            refresh_session_memory_task.apply_async(kwargs={"session_id": session.id, "trace_id": trace_id})
        await history.retention_cleanup(session.id)


def _plan_subtasks(prompt: str, max_steps: int = 3) -> list[str]:
    lines = [line.strip(" -\t") for line in prompt.splitlines() if line.strip()]
    if len(lines) >= 2:
        return lines[:max_steps]
    chunks = [part.strip() for part in prompt.split("。") if part.strip()]
    if len(chunks) >= 2:
        return chunks[:max_steps]
    return [prompt.strip()[:1200]]


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
) -> tuple[str, dict[str, int] | None]:
    subtasks = _plan_subtasks(prompt)
    step_timeout_seconds = max(
        1,
        min(settings.workflow_step_timeout_seconds_default, settings.workflow_step_timeout_seconds_max),
    )
    step_max_retries = max(0, settings.workflow_step_max_retries)
    checkpoints = await task_manager.list_checkpoints(task_id, limit=200)
    completed_from_state = sum(1 for item in checkpoints if item.get("kind") == "step_completed")
    start_idx = resume_from_step if resume_from_step is not None else completed_from_state
    start_idx = max(0, min(start_idx, len(subtasks)))
    outputs: list[str] = [item.get("output", "") for item in checkpoints if item.get("kind") == "step_completed"]
    outputs = outputs[:start_idx]

    for idx in range(start_idx, len(subtasks)):
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

        worker_name = "research_worker" if idx % 2 == 0 else "report_worker"
        subtask = subtasks[idx]
        await task_manager.save_checkpoint(
            task_id,
            {"kind": "step_started", "step": idx, "worker": worker_name, "prompt": subtask[:300]},
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
                    "subtask": subtask[:500],
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
                if model != "builtin":
                    worker_output, _ = await asyncio.wait_for(
                        _run_openai_stream_task(
                            task_id=task_id,
                            model=model,
                            prompt=f"[WorkerTask]\n{subtask}",
                            trace_id=trace_id,
                            tenant_id=tenant_id,
                            session_id=session_id,
                            instructions=f"{instructions}\n\nYou are {worker_name}. Focus only on current subtask.",
                            summary_version=summary_version,
                            context_budget_used=context_budget_used,
                            tools_specs=tools_specs,
                            user_id=user_id,
                            agent_type=worker_name,
                            suppress_terminal_events=True,
                        ),
                        timeout=step_timeout_seconds,
                    )
                else:
                    async def _run_builtin_worker() -> str:
                        local_output = ""
                        agent = create_agent(task_id=task_id, agent_type="echo", model="builtin")
                        result = await agent.run(prompt=f"[{worker_name}] {subtask}")
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
                break
            except asyncio.TimeoutError:
                await task_manager.save_checkpoint(
                    task_id,
                    {
                        "kind": "step_timeout",
                        "step": idx,
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
                                "trace_id": trace_id,
                                "tenant_id": tenant_id,
                                "session_id": session_id,
                            },
                        )
                    )
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
                                "trace_id": trace_id,
                                "tenant_id": tenant_id,
                                "session_id": session_id,
                            },
                        )
                    )
                    return "", None
                attempt += 1
                await asyncio.sleep(settings.workflow_step_retry_backoff_seconds * attempt)

        outputs.append(worker_output)
        await task_manager.save_checkpoint(
            task_id,
            {
                "kind": "step_completed",
                "step": idx,
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

    tool_context = ToolExecutionContext(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        trace_id=trace_id,
        task_id=task_id,
        permissions={"session:read"},
        approved_tools=await task_manager.approved_tools(task_id),
    )
    if tenant_id != "public":
        tool_context.permissions.add("http:external")
        tool_context.permissions.add("mcp:invoke")
    tools_executor = ToolExecutor(tool_registry)
    openai_tools = build_openai_agent_tools(
        specs=tools_specs or [],
        executor=tools_executor,
        context=tool_context,
        on_tool_event=on_tool_event,
    )

    try:
        full_text, usage = await stream_openai_agents(
            prompt=prompt,
            model=model,
            instructions=final_instructions,
            on_delta=on_delta,
            should_stop=lambda: task_manager.cancelled(task_id),
            tools=openai_tools,
        )
    except RuntimeError as exc:
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
        raise

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
    )


async def run_demo_task_inline(task_id: str) -> None:
    await _run_demo_task(task_id=task_id)


async def run_scan_overdue_approval_tickets_inline() -> dict[str, int]:
    return await _scan_overdue_approval_tickets()


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


def _render_model_input(snapshot: AgentContextSnapshot) -> str:
    if snapshot.context_blocks:
        return f"{snapshot.instructions}\n\n" + "\n\n".join(snapshot.context_blocks) + f"\n\n[CurrentUserMessage]\n{snapshot.input_message}"
    return f"{snapshot.instructions}\n\n[CurrentUserMessage]\n{snapshot.input_message}"
