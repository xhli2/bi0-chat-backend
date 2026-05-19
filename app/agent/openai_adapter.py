import importlib
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


def _read_attr_or_key(obj: Any, field: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(field)
    return getattr(obj, field, None)


def _extract_delta(event: Any) -> str | None:
    for field in ("delta", "text", "content"):
        value = _read_attr_or_key(event, field)
        if isinstance(value, str) and value:
            return value

    data = _read_attr_or_key(event, "data")
    if data is not None:
        for field in ("delta", "text", "content"):
            value = _read_attr_or_key(data, field)
            if isinstance(value, str) and value:
                return value
    return None


def _extract_usage(event: Any) -> dict[str, int] | None:
    usage = _read_attr_or_key(event, "usage")
    if usage is None:
        usage = _read_attr_or_key(_read_attr_or_key(event, "data"), "usage")
    if usage is None:
        return None

    input_tokens = _read_attr_or_key(usage, "input_tokens") or _read_attr_or_key(usage, "prompt_tokens")
    output_tokens = _read_attr_or_key(usage, "output_tokens") or _read_attr_or_key(usage, "completion_tokens")
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        return {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return None


def _as_async_iterator(stream_session: Any) -> AsyncIterator[Any] | None:
    for field in ("stream_events", "events"):
        value = getattr(stream_session, field, None)
        if callable(value):
            iterator = value()
            if hasattr(iterator, "__aiter__"):
                return iterator
    if hasattr(stream_session, "__aiter__"):
        return stream_session
    return None


async def _resolve_final_output(result: Any) -> str:
    final_output = getattr(result, "final_output", None)
    if inspect.isawaitable(final_output):
        final_output = await final_output
    if isinstance(final_output, str) and final_output:
        return final_output
    return str(result)


async def run_openai_agents(prompt: str, model: str, instructions: str) -> str:
    agents_module = importlib.import_module("agents")
    agent_cls = getattr(agents_module, "Agent")
    runner_cls = getattr(agents_module, "Runner")

    agent = agent_cls(name="report-agent", instructions=instructions, model=model)
    result = await runner_cls.run(agent, prompt)
    return await _resolve_final_output(result)


async def stream_openai_agents(
    prompt: str,
    model: str,
    instructions: str,
    on_delta: Callable[[str], Awaitable[None]],
    should_stop: Callable[[], Awaitable[bool]] | None = None,
    tools: list[Any] | None = None,
) -> tuple[str, dict[str, int] | None]:
    agents_module = importlib.import_module("agents")
    agent_cls = getattr(agents_module, "Agent")
    runner_cls = getattr(agents_module, "Runner")
    run_streamed = getattr(runner_cls, "run_streamed", None)

    agent_kwargs = {"name": "report-agent", "instructions": instructions, "model": model}
    if tools:
        agent_kwargs["tools"] = tools
    agent = agent_cls(**agent_kwargs)
    chunks: list[str] = []
    usage_totals: dict[str, int] | None = None

    if callable(run_streamed):
        stream_session = run_streamed(agent, prompt)
        if inspect.isawaitable(stream_session):
            stream_session = await stream_session

        iterator = _as_async_iterator(stream_session)
        if iterator is not None:
            async for event in iterator:
                if should_stop and await should_stop():
                    break

                delta = _extract_delta(event)
                if delta:
                    chunks.append(delta)
                    await on_delta(delta)

                usage = _extract_usage(event)
                if usage:
                    usage_totals = usage

            final_output = await _resolve_final_output(stream_session)
            if not chunks and final_output:
                chunks.append(final_output)
                await on_delta(final_output)
            return "".join(chunks), usage_totals

    result = await runner_cls.run(agent, prompt)
    final_output = await _resolve_final_output(result)
    if final_output:
        chunks.append(final_output)
        await on_delta(final_output)
    usage_totals = _extract_usage(result)
    return "".join(chunks), usage_totals
