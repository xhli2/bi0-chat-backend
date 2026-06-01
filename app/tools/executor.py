from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.agent.hooks import run_post_tool_hooks, run_pre_tool_hooks
from app.core.config import get_settings
from app.core.telemetry import telemetry
from app.db.session import SessionLocal
from app.services.session_persistence import SessionPersistenceService, _output_ref_from_payload
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolExecutionContext, ToolExecutionError, ToolExecutionResult


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.settings = get_settings()

    async def execute(self, tool_name: str, args: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        started_at = time.perf_counter()

        def _finalize(result: ToolExecutionResult) -> ToolExecutionResult:
            duration_ms = result.duration_ms or int((time.perf_counter() - started_at) * 1000)
            result.duration_ms = duration_ms
            telemetry.record_tool_outcome(
                tool_name=tool_name,
                tenant_id=context.tenant_id,
                ok=result.ok,
                error_code=result.error_code,
                duration_ms=duration_ms,
            )
            return result

        async def _done(result: ToolExecutionResult, output: Any | None = None) -> ToolExecutionResult:
            finalized = _finalize(result)
            await self._persist_execution(
                tool_name=tool_name,
                args=args,
                context=context,
                result=finalized,
                output=output if output is not None else finalized.data,
            )
            return finalized

        with telemetry.span("tool.execute", tool_name=tool_name, tenant_id=context.tenant_id):
            spec = self.registry.get(tool_name)
            if spec is None:
                return await _done(ToolExecutionResult(ok=False, error_code="TOOL_NOT_FOUND", message=f"Unknown tool: {tool_name}"))

            if not self.registry.is_allowed_for_tenant(context.tenant_id, tool_name):
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_NOT_ALLOWED_FOR_TENANT",
                    message=f"Tenant '{context.tenant_id}' cannot use tool '{tool_name}'",
                ))

            missing_permissions = [perm for perm in spec.required_permissions if perm not in context.permissions]
            if missing_permissions:
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_PERMISSION_DENIED",
                    message=f"Missing permissions: {', '.join(sorted(missing_permissions))}",
                ))

            if self._has_blocked_pattern(args):
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_GUARDRAIL_BLOCKED",
                    message="Tool input blocked by guardrail policy.",
                ))

            if self._requires_approval(spec=spec, context=context):
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_APPROVAL_REQUIRED",
                    message=f"Tool '{tool_name}' requires approval before execution.",
                    retryable=False,
                ))

            if context.allowed_tools is not None and tool_name not in context.allowed_tools:
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code="SUBAGENT_TOOL_FORBIDDEN",
                    message=f"Tool '{tool_name}' is not allowed in this execution context.",
                ))

            hook_metadata = {
                "tenant_id": context.tenant_id,
                "session_id": context.session_id,
                "task_id": context.task_id,
                "trace_id": context.trace_id,
                "allowed_tools": sorted(context.allowed_tools) if context.allowed_tools else None,
            }

            try:
                run_pre_tool_hooks(tool_name, args, metadata=hook_metadata)
            except ToolExecutionError as exc:
                elapsed = int((time.perf_counter() - started_at) * 1000)
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    duration_ms=elapsed,
                ))

            try:
                validated = spec.input_schema.model_validate(args)
            except ValidationError as exc:
                return await _done(ToolExecutionResult(ok=False, error_code="TOOL_INPUT_VALIDATION_ERROR", message=str(exc)))

            timeout_seconds = min(spec.timeout_seconds, self.settings.tool_call_timeout_seconds_max)
            timeout_seconds = max(1, timeout_seconds)
            try:
                result_raw = await asyncio.wait_for(
                    spec.executor(validated.model_dump(mode="python"), {"context": context}),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                elapsed = int((time.perf_counter() - started_at) * 1000)
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_TIMEOUT",
                    message=f"Tool '{tool_name}' timed out after {timeout_seconds}s",
                    retryable=True,
                    duration_ms=elapsed,
                ))
            except ToolExecutionError as exc:
                elapsed = int((time.perf_counter() - started_at) * 1000)
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    duration_ms=elapsed,
                ))
            except Exception as exc:  # noqa: BLE001
                elapsed = int((time.perf_counter() - started_at) * 1000)
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_RUNTIME_ERROR",
                    message=f"{type(exc).__name__}: {exc}",
                    retryable=False,
                    duration_ms=elapsed,
                ))

            if spec.output_schema is not None:
                try:
                    output = spec.output_schema.model_validate(result_raw).model_dump(mode="python")
                except ValidationError as exc:
                    elapsed = int((time.perf_counter() - started_at) * 1000)
                    return await _done(ToolExecutionResult(
                        ok=False,
                        error_code="TOOL_OUTPUT_VALIDATION_ERROR",
                        message=str(exc),
                        duration_ms=elapsed,
                    ))
            else:
                output = result_raw

            try:
                if isinstance(output, dict):
                    run_post_tool_hooks(tool_name, output, metadata=hook_metadata)
            except ToolExecutionError as exc:
                elapsed = int((time.perf_counter() - started_at) * 1000)
                return await _done(ToolExecutionResult(
                    ok=False,
                    error_code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    duration_ms=elapsed,
                ), output=output if isinstance(output, dict) else None)

            elapsed = int((time.perf_counter() - started_at) * 1000)
            return await _done(ToolExecutionResult(ok=True, data=output, duration_ms=elapsed), output=output)

    async def _persist_execution(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
        result: ToolExecutionResult,
        output: Any | None,
    ) -> None:
        if not context.session_id:
            return
        status = "success" if result.ok else "failed"
        if result.error_code == "TOOL_APPROVAL_REQUIRED":
            status = "pending_approval"
        call_id = str(uuid4())
        output_dict = output if isinstance(output, dict) else None
        async with SessionLocal() as db:
            persistence = SessionPersistenceService(db)
            record = await persistence.record_tool_call(
                session_id=context.session_id,
                task_id=context.task_id,
                trace_id=context.trace_id,
                tool_name=tool_name,
                call_id=call_id,
                input_json=args,
                output_json=output_dict,
                output_ref=_output_ref_from_payload(tool_name, output_dict),
                status=status,
                error_code=result.error_code,
                duration_ms=result.duration_ms,
            )
            if result.ok and output_dict:
                await persistence.ingest_tool_output(
                    session_id=context.session_id,
                    task_id=context.task_id,
                    turn_index=record.turn_index,
                    tool_call_id=record.call_id,
                    tool_name=tool_name,
                    output=output_dict,
                )

    def _has_blocked_pattern(self, args: dict[str, Any]) -> bool:
        payload = json.dumps(args, ensure_ascii=False).lower()
        for marker in self.settings.parsed_tool_guardrail_blocked_patterns:
            if marker and marker in payload:
                return True
        return False

    def _requires_approval(self, spec, context: ToolExecutionContext) -> bool:
        if spec.name in context.approved_tools:
            return False
        explicit_approval = spec.name in self.settings.parsed_tool_approval_required_tools
        high_risk_approval = spec.risk_level == "high"
        return explicit_approval or high_risk_approval
