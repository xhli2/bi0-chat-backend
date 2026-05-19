import asyncio
import json

import pytest
from pydantic import BaseModel, Field

from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.schemas import ToolExecutionContext, ToolExecutionError


class EchoInput(BaseModel):
    text: str = Field(min_length=1)


class EchoOutput(BaseModel):
    echoed: str


async def _echo_tool(args: dict, _: dict) -> dict:
    return {"echoed": args["text"]}


async def _sleep_tool(args: dict, _: dict) -> dict:
    await asyncio.sleep(args["seconds"])
    return {"ok": True}


@pytest.mark.asyncio
async def test_tool_executor_success():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo_tool",
            description="Echo tool",
            input_schema=EchoInput,
            output_schema=EchoOutput,
            required_permissions=set(),
            timeout_seconds=3,
            executor=_echo_tool,
        )
    )
    executor = ToolExecutor(registry)
    context = ToolExecutionContext(
        tenant_id="public",
        user_id=1,
        session_id="s1",
        trace_id="t1",
        task_id="task1",
        permissions=set(),
    )
    result = await executor.execute("echo_tool", {"text": "hello"}, context)
    assert result.ok is True
    assert result.data["echoed"] == "hello"


@pytest.mark.asyncio
async def test_tool_executor_permission_denied():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="secure_tool",
            description="Secure tool",
            input_schema=EchoInput,
            output_schema=EchoOutput,
            required_permissions={"secure:read"},
            timeout_seconds=3,
            executor=_echo_tool,
        )
    )
    executor = ToolExecutor(registry)
    context = ToolExecutionContext(
        tenant_id="public",
        user_id=1,
        session_id="s1",
        trace_id="t1",
        task_id="task1",
        permissions=set(),
    )
    result = await executor.execute("secure_tool", {"text": "hello"}, context)
    assert result.ok is False
    assert result.error_code == "TOOL_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_tool_executor_timeout_and_tenant_policy():
    class SleepInput(BaseModel):
        seconds: int

    registry = ToolRegistry()
    registry._settings.tenant_tool_policies_json = json.dumps({"public": ["safe_tool"]})
    registry.register(
        ToolSpec(
            name="slow_tool",
            description="Slow tool",
            input_schema=SleepInput,
            output_schema=None,
            required_permissions=set(),
            timeout_seconds=1,
            executor=_sleep_tool,
        )
    )
    context = ToolExecutionContext(
        tenant_id="public",
        user_id=1,
        session_id="s1",
        trace_id="t1",
        task_id="task1",
        permissions=set(),
    )
    executor = ToolExecutor(registry)
    denied = await executor.execute("slow_tool", {"seconds": 2}, context)
    assert denied.ok is False
    assert denied.error_code == "TOOL_NOT_ALLOWED_FOR_TENANT"


@pytest.mark.asyncio
async def test_tool_executor_error_mapping():
    class DummyInput(BaseModel):
        value: int

    async def _failing(_: dict, __: dict) -> dict:
        raise ToolExecutionError("DOWNSTREAM_FAILED", "downstream fail", retryable=True)

    registry = ToolRegistry()
    registry._settings.tenant_tool_policies_json = "{}"
    registry.register(
        ToolSpec(
            name="failing_tool",
            description="failing",
            input_schema=DummyInput,
            output_schema=None,
            required_permissions=set(),
            timeout_seconds=3,
            executor=_failing,
        )
    )
    context = ToolExecutionContext(
        tenant_id="public",
        user_id=1,
        session_id="s1",
        trace_id="t1",
        task_id="task1",
        permissions=set(),
    )
    result = await ToolExecutor(registry).execute("failing_tool", {"value": 1}, context)
    assert result.ok is False
    assert result.error_code == "DOWNSTREAM_FAILED"
    assert result.retryable is True
