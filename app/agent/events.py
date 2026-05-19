from app.schemas.realtime import AgentEvent


def build_status_event(event_id: str, task_id: str, status: str, message: str) -> AgentEvent:
    return AgentEvent(
        id=event_id,
        type="status",
        task_id=task_id,
        payload={"status": status, "message": message},
    )


def build_delta_event(event_id: str, task_id: str, chunk: str) -> AgentEvent:
    return AgentEvent(
        id=event_id,
        type="delta",
        task_id=task_id,
        payload={"chunk": chunk},
    )


def build_part_event(event_id: str, task_id: str, name: str, content: str) -> AgentEvent:
    return AgentEvent(
        id=event_id,
        type="part",
        task_id=task_id,
        payload={"name": name, "content": content},
    )


def build_usage_event(event_id: str, task_id: str, input_tokens: int, output_tokens: int) -> AgentEvent:
    return AgentEvent(
        id=event_id,
        type="usage",
        task_id=task_id,
        payload={"input_tokens": input_tokens, "output_tokens": output_tokens},
    )
