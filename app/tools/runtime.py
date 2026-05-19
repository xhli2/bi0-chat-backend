from __future__ import annotations

import importlib
from typing import Any
from uuid import uuid4

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
    agents_module = importlib.import_module("agents")
    function_tool = getattr(agents_module, "function_tool", None)
    built: list[Any] = []

    for spec in specs:
        async def _wrapped_tool(_tool_name: str = spec.name, **kwargs):
            call_id = str(uuid4())
            safe_args = redact_args(kwargs)
            await on_tool_event(
                "tool_start",
                {
                    "tool_name": _tool_name,
                    "call_id": call_id,
                    "args_preview": safe_args,
                    "trace_id": context.trace_id,
                    "session_id": context.session_id,
                },
            )
            result = await executor.execute(_tool_name, kwargs, context)
            if result.ok:
                await on_tool_event(
                    "tool_end",
                    {
                        "tool_name": _tool_name,
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
                        "tool_name": _tool_name,
                        "call_id": call_id,
                        "message": result.message,
                        "args_preview": safe_args,
                    },
                )
                raise RuntimeError(result.message or "Tool approval required")

            await on_tool_event(
                "tool_error",
                {
                    "tool_name": _tool_name,
                    "call_id": call_id,
                    "error_code": result.error_code,
                    "message": result.message,
                    "retryable": result.retryable,
                },
            )
            raise RuntimeError(result.message or "Tool call failed")

        _wrapped_tool.__name__ = f"tool_{spec.name}"
        _wrapped_tool.__doc__ = spec.description
        if callable(function_tool):
            try:
                built.append(function_tool(_wrapped_tool))
                continue
            except Exception:
                pass
        built.append(_wrapped_tool)
    return built
