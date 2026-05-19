import json

import pytest


async def _collect_events(client, url: str, max_events: int = 5, headers: dict[str, str] | None = None):
    events = []
    current_event_type = None
    async with client.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event_type = line.split(":", 1)[1].strip()
            if line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip())
                events.append((current_event_type, payload))
                if len(events) >= max_events:
                    break
    return events


@pytest.mark.asyncio
async def test_task_stream_and_cancel(client, auth_headers):
    create_response = await client.post("/api/v1/tasks", headers=auth_headers)
    assert create_response.status_code == 200
    task_id = create_response.json()["task_id"]

    events = await _collect_events(client, f"/api/v1/tasks/{task_id}/stream", max_events=3, headers=auth_headers)
    assert any(event_type == "status" for event_type, _ in events)

    cancel_response = await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=auth_headers)
    assert cancel_response.status_code == 200
    assert cancel_response.json()["interrupted"] is True


@pytest.mark.asyncio
async def test_agent_protocol_events(client, auth_headers):
    run_response = await client.post(
        "/api/v1/agents/run",
        json={"agent_type": "echo", "prompt": "hello world"},
        headers=auth_headers,
    )
    assert run_response.status_code == 200
    stream_url = run_response.json()["stream_url"]

    events = await _collect_events(client, stream_url, max_events=300, headers=auth_headers)
    event_types = {event_type for event_type, _ in events}
    assert "status" in event_types
    assert "delta" in event_types
    assert "part" in event_types
    assert "usage" in event_types
