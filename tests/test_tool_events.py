import pytest
from pydantic import BaseModel

from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.runtime import build_openai_agent_tools
from app.tools.schemas import ToolExecutionContext, ToolExecutionError


class ToolInput(BaseModel):
    value: str


async def _ok_tool(args: dict, _: dict) -> dict:
    return {"value": args["value"].upper()}


async def _err_tool(_: dict, __: dict) -> dict:
    raise ToolExecutionError("SIMULATED", "simulated failure")


def _make_flaky_tool():
    state = {"attempt": 0}

    async def _flaky(args: dict, _: dict) -> dict:
        state["attempt"] += 1
        if state["attempt"] == 1:
            raise ToolExecutionError("TRANSIENT", "temporary", retryable=True)
        return {"value": args["value"][::-1]}

    return _flaky, state


@pytest.mark.asyncio
async def test_tool_event_order_success():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="ok_tool",
            description="ok tool",
            input_schema=ToolInput,
            output_schema=None,
            required_permissions=set(),
            timeout_seconds=5,
            executor=_ok_tool,
        )
    )
    executor = ToolExecutor(registry)
    context = ToolExecutionContext(
        tenant_id="public",
        user_id=1,
        session_id="session-1",
        trace_id="trace-1",
        task_id="task-1",
        permissions=set(),
    )
    events: list[tuple[str, dict]] = []

    async def on_tool_event(kind: str, payload: dict):
        events.append((kind, payload))

    spec = registry.get("ok_tool")
    assert spec is not None
    wrappers = build_openai_agent_tools([spec], executor, context, on_tool_event)
    result = await wrappers[0](value="abc")

    assert result["value"] == "ABC"
    assert len(events) == 2
    assert events[0][0] == "tool_start"
    assert events[1][0] == "tool_end"
    assert events[0][1]["tool_name"] == "ok_tool"
    assert events[1][1]["tool_name"] == "ok_tool"


@pytest.mark.asyncio
async def test_tool_event_order_error():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="err_tool",
            description="err tool",
            input_schema=ToolInput,
            output_schema=None,
            required_permissions=set(),
            timeout_seconds=5,
            executor=_err_tool,
        )
    )
    executor = ToolExecutor(registry)
    context = ToolExecutionContext(
        tenant_id="public",
        user_id=1,
        session_id="session-1",
        trace_id="trace-1",
        task_id="task-1",
        permissions=set(),
    )
    events: list[tuple[str, dict]] = []

    async def on_tool_event(kind: str, payload: dict):
        events.append((kind, payload))

    spec = registry.get("err_tool")
    assert spec is not None
    wrappers = build_openai_agent_tools([spec], executor, context, on_tool_event)
    with pytest.raises(RuntimeError):
        await wrappers[0](value="abc")

    assert len(events) == 2
    assert events[0][0] == "tool_start"
    assert events[1][0] == "tool_error"


@pytest.mark.asyncio
async def test_tool_retry_and_idempotent_cache():
    flaky, state = _make_flaky_tool()
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="flaky_tool",
            description="flaky tool",
            input_schema=ToolInput,
            output_schema=None,
            required_permissions=set(),
            timeout_seconds=5,
            executor=flaky,
        )
    )
    executor = ToolExecutor(registry)
    context = ToolExecutionContext(
        tenant_id="public",
        user_id=1,
        session_id="session-1",
        trace_id="trace-1",
        task_id="task-1",
        permissions=set(),
    )
    events: list[tuple[str, dict]] = []

    async def on_tool_event(kind: str, payload: dict):
        events.append((kind, payload))

    spec = registry.get("flaky_tool")
    assert spec is not None
    wrappers = build_openai_agent_tools([spec], executor, context, on_tool_event)
    result_first = await wrappers[0](value="abc")
    result_second = await wrappers[0](value="abc")
    assert result_first["value"] == "cba"
    assert result_second["value"] == "cba"
    assert state["attempt"] == 2
    assert any(kind == "tool_error" for kind, _ in events)
    assert any(kind == "tool_end" and payload.get("cached") for kind, payload in events)
