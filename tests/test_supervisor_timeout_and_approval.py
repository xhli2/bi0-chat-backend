from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db.session import SessionLocal
from app.schemas.realtime import AgentEvent
from app.services.approval_flow import ApprovalFlowService
from app.services.task_manager import task_manager
from app.worker import tasks as worker_tasks


@pytest.mark.asyncio
async def test_supervisor_step_timeout_emits_event(monkeypatch):
    task_id = await task_manager.create_task(
        priority="default",
        model="builtin",
        tenant_id="public",
        trace_id=str(uuid4()),
        user_id=1,
        owner_id=1,
    )

    class SlowAgent:
        async def run(self, prompt: str):
            _ = prompt
            await worker_tasks.asyncio.sleep(1.5)
            return type("R", (), {"events": [AgentEvent(id=str(uuid4()), type="status", task_id=task_id, payload={"status": "running"})]})

    old_timeout = worker_tasks.settings.workflow_step_timeout_seconds_default
    old_retries = worker_tasks.settings.workflow_step_max_retries
    worker_tasks.settings.workflow_step_timeout_seconds_default = 1
    worker_tasks.settings.workflow_step_max_retries = 0
    monkeypatch.setattr(worker_tasks, "create_agent", lambda **_: SlowAgent())

    try:
        final_text, usage = await worker_tasks._run_supervisor_workflow(
            task_id=task_id,
            agent_type="supervisor",
            model="builtin",
            prompt="step-1\nstep-2",
            tenant_id="public",
            trace_id=str(uuid4()),
            session_id=None,
            user_id=1,
            instructions="test",
            context_budget_used=0,
            summary_version=None,
            tools_specs=[],
            resume_from_step=None,
        )
    finally:
        worker_tasks.settings.workflow_step_timeout_seconds_default = old_timeout
        worker_tasks.settings.workflow_step_max_retries = old_retries

    assert final_text == ""
    assert usage is None
    events = await task_manager.recent_events(task_id, limit=50)
    assert any(event.type == "step_timeout" for event in events)


@pytest.mark.asyncio
async def test_approval_ticket_overdue_scan():
    async with SessionLocal() as db:
        service = ApprovalFlowService(db)
        ticket = await service.create_ticket(
            task_id=str(uuid4()),
            tenant_id="public",
            tool_name="http_search_wrapper",
            requested_by=1,
            trace_id=str(uuid4()),
            reason="need approval",
        )
        ticket.due_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.commit()

        scanned, overdue = await service.scan_overdue()
        refreshed = await service.get_ticket(ticket.id)

    assert scanned >= 1
    assert overdue >= 1
    assert refreshed is not None
    assert refreshed.status == "overdue"
