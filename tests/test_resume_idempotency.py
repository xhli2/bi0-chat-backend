import pytest

from app.db.session import SessionLocal
from app.services.session_history import SessionHistoryService
from app.services.task_manager import task_manager
from app.worker.tasks import run_agent_task_inline


@pytest.mark.asyncio
async def test_resume_skips_duplicate_user_message():
    async with SessionLocal() as db:
        history = SessionHistoryService(db)
        session = await history.ensure_session(session_id=None, tenant_id="public", user_id=1)

    task_id = await task_manager.create_task(
        priority="default",
        model="builtin",
        tenant_id="public",
        trace_id="trace-idempotent",
        session_id=session.id,
        user_id=1,
        owner_id=1,
    )
    prompt = "hello resume idempotency"
    await run_agent_task_inline(
        task_id=task_id,
        agent_type="echo",
        prompt=prompt,
        model="builtin",
        tenant_id="public",
        trace_id="trace-idempotent",
        session_id=session.id,
        user_id=1,
        persist_user_message=True,
        permissions={"session:read"},
        scopes={"agent:run"},
    )
    await run_agent_task_inline(
        task_id=task_id,
        agent_type="echo",
        prompt=prompt,
        model="builtin",
        tenant_id="public",
        trace_id="trace-idempotent",
        session_id=session.id,
        user_id=1,
        persist_user_message=False,
        permissions={"session:read"},
        scopes={"agent:run"},
    )
    async with SessionLocal() as db:
        history = SessionHistoryService(db)
        messages = await history.list_messages(session.id, page=1, size=100, include_archived=True)
    user_prompt_messages = [m for m in messages if m.role == "user" and m.content == prompt]
    assert len(user_prompt_messages) == 1
