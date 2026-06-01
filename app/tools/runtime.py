from __future__ import annotations

import importlib
import asyncio
import json
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolSpec
from app.tools.schemas import ToolExecutionContext


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    sensitive_markers = {"password", "secret", "token", "api_key", "authorization"}
    for key, value in args.items():
        lowered = key.lower()
        if any(marker in lowered for marker in sensitive_markers):
            redacted[key] = "***"
        else:
            redacted[key] = value
    return redacted


def build_openai_agent_tools(
    specs: list[ToolSpec],
    executor: ToolExecutor,
    context: ToolExecutionContext,
    on_tool_event,
) -> list[Any]:
    settings = get_settings()
    agents_module = importlib.import_module("agents")
    function_tool = getattr(agents_module, "function_tool", None)
    built: list[Any] = []

    for spec in specs:
        def _make_tool(tool_name: str, tool_spec: ToolSpec):
            async def _wrapped_tool(**kwargs):
                call_id = str(uuid4())
                safe_args = redact_args(kwargs)
                try:
                    key_payload = json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
                except TypeError:
                    key_payload = str(sorted(kwargs.items()))
                idempotency_key = f"{tool_name}:{key_payload}"
                if idempotency_key in context.tool_result_cache:
                    cached_data = context.tool_result_cache[idempotency_key]
                    await on_tool_event(
                        "tool_end",
                        {
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "duration_ms": 0,
                            "cached": True,
                            "result_preview": str(cached_data)[:500],
                        },
                    )
                    return cached_data
                await on_tool_event(
                    "tool_start",
                    {
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "args_preview": safe_args,
                        "trace_id": context.trace_id,
                        "session_id": context.session_id,
                    },
                )
                attempts = max(1, settings.tool_call_retry_attempts)
                result = await executor.execute(tool_name, kwargs, context)
                current_attempt = 1
                while (not result.ok) and result.retryable and current_attempt < attempts:
                    await on_tool_event(
                        "tool_error",
                        {
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "error_code": result.error_code,
                            "message": result.message,
                            "retryable": True,
                            "retry_attempt": current_attempt,
                        },
                    )
                    await asyncio.sleep(settings.tool_call_retry_backoff_seconds * current_attempt)
                    current_attempt += 1
                    result = await executor.execute(tool_name, kwargs, context)
                if result.ok:
                    context.tool_result_cache[idempotency_key] = result.data
                    await on_tool_event(
                        "tool_end",
                        {
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "duration_ms": result.duration_ms,
                            "result_preview": str(result.data)[:500],
                        },
                    )
                    return result.data

                if result.error_code == "TOOL_APPROVAL_REQUIRED":
                    await on_tool_event(
                        "approval_required",
                        {
                            "tool_name": tool_name,
                            "call_id": call_id,
                            "message": result.message,
                            "args_preview": safe_args,
                        },
                    )
                    raise RuntimeError(result.message or "Tool approval required")

                await on_tool_event(
                    "tool_error",
                    {
                        "tool_name": tool_name,
                        "call_id": call_id,
                        "error_code": result.error_code,
                        "message": result.message,
                        "retryable": result.retryable,
                    },
                )
                raise RuntimeError(result.message or "Tool call failed")

            _wrapped_tool.__name__ = f"tool_{tool_name}"
            _wrapped_tool.__doc__ = tool_spec.description
            return _wrapped_tool

        wrapped = _make_tool(spec.name, spec)
        if callable(function_tool):
            built.append(
                function_tool(
                    wrapped,
                    name_override=spec.name,
                    description_override=spec.description,
                    strict_mode=False,
                )
            )
        else:
            built.append(wrapped)
    return built
