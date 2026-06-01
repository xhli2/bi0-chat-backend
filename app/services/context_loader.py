from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.session import AgentContextSnapshot, ContextPolicy
from app.services.prompt_builder import PromptBuildInput, PromptBuilder
from app.services.session_history import SessionHistoryService


class ContextLoader:
    def __init__(self, db: AsyncSession) -> None:
        self.history = SessionHistoryService(db)
        self.builder = PromptBuilder()

    async def load_snapshot(
        self,
        session_id: str,
        tenant_id: str,
        user_prompt: str,
        context_policy: ContextPolicy,
        agent_type: str = "echo",
    ) -> AgentContextSnapshot:
        recent_messages = await self.history.get_recent_messages(session_id, limit=self.history.settings.context_recent_message_limit)
        latest_summary = await self.history.get_latest_summary(session_id)
        memory_items = await self.history.get_memory(session_id, limit=self.history.settings.max_kv_per_session)
        active_entities = await self.history.list_entities(session_id, active_only=True)
        recent_tool_calls = await self.history.list_tool_calls(session_id, limit=20)
        return self.builder.build(
            PromptBuildInput(
                session_id=session_id,
                tenant_id=tenant_id,
                user_prompt=user_prompt,
                context_policy=context_policy,
                summary=latest_summary,
                memory_items=memory_items,
                recent_messages=recent_messages,
                active_entities=active_entities,
                recent_tool_calls=recent_tool_calls,
                agent_type=agent_type,
            )
        )
