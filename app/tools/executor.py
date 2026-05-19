from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.telemetry import telemetry
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolExecutionContext, ToolExecutionError, ToolExecutionResult


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.settings = get_settings()

    async def execute(self, tool_name: str, args: dict[str, Any], context: ToolExecutionContext) -> ToolExecutionResult:
        started_at = time.perf_counter()
        with telemetry.span("tool.execute", tool_name=tool_name, tenant_id=context.tenant_id):
            spec = self.registry.get(tool_name)
            if spec is None:
                return ToolExecutionResult(ok=False, error_code="TOOL_NOT_FOUND", message=f"Unknown tool: {tool_name}")

            if not self.registry.is_allowed_for_tenant(context.tenant_id, tool_name):
                return ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_NOT_ALLOWED_FOR_TENANT",
                    message=f"Tenant '{context.tenant_id}' cannot use tool '{tool_name}'",
                )

            missing_permissions = [perm for perm in spec.required_permissions if perm not in context.permissions]
            if missing_permissions:
                return ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_PERMISSION_DENIED",
                    message=f"Missing permissions: {', '.join(sorted(missing_permissions))}",
                )

            if self._has_blocked_pattern(args):
                return ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_GUARDRAIL_BLOCKED",
                    message="Tool input blocked by guardrail policy.",
                )

            if self._requires_approval(spec=spec, context=context):
                return ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_APPROVAL_REQUIRED",
                    message=f"Tool '{tool_name}' requires approval before execution.",
                    retryable=False,
                )

            try:
                validated = spec.input_schema.model_validate(args)
            except ValidationError as exc:
                return ToolExecutionResult(ok=False, error_code="TOOL_INPUT_VALIDATION_ERROR", message=str(exc))

            timeout_seconds = min(spec.timeout_seconds, self.settings.tool_call_timeout_seconds_max)
            timeout_seconds = max(1, timeout_seconds)
            try:
                result_raw = await asyncio.wait_for(
                    spec.executor(validated.model_dump(mode="python"), {"context": context}),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                elapsed = int((time.perf_counter() - started_at) * 1000)
                return ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_TIMEOUT",
                    message=f"Tool '{tool_name}' timed out after {timeout_seconds}s",
                    retryable=True,
                    duration_ms=elapsed,
                )
            except ToolExecutionError as exc:
                elapsed = int((time.perf_counter() - started_at) * 1000)
                return ToolExecutionResult(
                    ok=False,
                    error_code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    duration_ms=elapsed,
                )
            except Exception as exc:  # noqa: BLE001
                elapsed = int((time.perf_counter() - started_at) * 1000)
                return ToolExecutionResult(
                    ok=False,
                    error_code="TOOL_RUNTIME_ERROR",
                    message=f"{type(exc).__name__}: {exc}",
                    retryable=False,
                    duration_ms=elapsed,
                )

            if spec.output_schema is not None:
                try:
                    output = spec.output_schema.model_validate(result_raw).model_dump(mode="python")
                except ValidationError as exc:
                    elapsed = int((time.perf_counter() - started_at) * 1000)
                    return ToolExecutionResult(
                        ok=False,
                        error_code="TOOL_OUTPUT_VALIDATION_ERROR",
                        message=str(exc),
                        duration_ms=elapsed,
                    )
            else:
                output = result_raw

            elapsed = int((time.perf_counter() - started_at) * 1000)
            return ToolExecutionResult(ok=True, data=output, duration_ms=elapsed)

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
