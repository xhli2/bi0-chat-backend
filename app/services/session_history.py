from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ApiError
from app.models.chat_memory_kv import ChatMemoryKV
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.chat_summary import ChatSummary
from app.models.chat_tool_call import ChatToolCall
from app.models.session_entity import SessionEntity
from app.models.session_run import SessionRun
from app.schemas.session import ContextPolicy, TokenUsageBreakdown
from app.services.token_counter import estimate_tokens_for_model


def estimate_tokens(text: str, model: str | None = None) -> int:
    return estimate_tokens_for_model(text, model=model)


class SessionHistoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings()

    async def create_session(self, tenant_id: str, user_id: int | None, title: str = "New Session") -> ChatSession:
        session = ChatSession(id=str(uuid4()), tenant_id=tenant_id, user_id=user_id, title=title, status="active")
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def ensure_session(self, session_id: str | None, tenant_id: str, user_id: int | None) -> ChatSession:
        if session_id:
            existing = await self.get_session(session_id=session_id, tenant_id=tenant_id, user_id=user_id)
            if existing:
                return existing
        return await self.create_session(tenant_id=tenant_id, user_id=user_id, title="New Session")

    async def get_session(self, session_id: str, tenant_id: str | None = None, user_id: int | None = None) -> ChatSession | None:
        result = await self.db.execute(select(ChatSession).where(ChatSession.id == session_id))
        session = result.scalars().first()
        if session is None:
            return None
        if tenant_id is not None and session.tenant_id != tenant_id:
            raise ApiError(status_code=403, code="SESSION_TENANT_MISMATCH", detail="Session does not belong to tenant.")
        if user_id is not None and session.user_id != user_id:
            raise ApiError(status_code=403, code="SESSION_USER_MISMATCH", detail="Session does not belong to user.")
        return session

    async def list_sessions(self, tenant_id: str, user_id: int | None, limit: int = 20) -> list[ChatSession]:
        stmt: Select[tuple[ChatSession]] = (
            select(ChatSession)
            .where(ChatSession.tenant_id == tenant_id)
            .order_by(ChatSession.last_active_at.desc())
            .limit(limit)
        )
        if user_id is not None:
            stmt = stmt.where(ChatSession.user_id == user_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        trace_id: str | None,
        token_estimate: int | None = None,
        task_id: str | None = None,
        metadata: dict | None = None,
    ) -> ChatMessage:
        next_turn = await self.next_turn_index(session_id)
        message = ChatMessage(
            id=str(uuid4()),
            session_id=session_id,
            turn_index=next_turn,
            role=role,
            content=content,
            trace_id=trace_id,
            task_id=task_id,
            metadata_json=metadata or {},
            token_estimate=token_estimate if token_estimate is not None else estimate_tokens(content),
        )
        self.db.add(message)
        await self.db.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(last_active_at=datetime.now(timezone.utc))
        )
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def next_turn_index(self, session_id: str) -> int:
        result = await self.db.execute(select(func.max(ChatMessage.turn_index)).where(ChatMessage.session_id == session_id))
        current = result.scalar_one_or_none()
        return (current or 0) + 1

    async def list_messages(
        self,
        session_id: str,
        page: int = 1,
        size: int = 50,
        include_archived: bool = False,
    ) -> list[ChatMessage]:
        offset = (page - 1) * size
        stmt: Select[tuple[ChatMessage]] = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.turn_index.asc())
            .offset(offset)
            .limit(size)
        )
        if not include_archived:
            stmt = stmt.where(ChatMessage.is_archived.is_(False))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_messages(self, session_id: str, limit: int) -> list[ChatMessage]:
        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.is_archived.is_(False))
            .order_by(ChatMessage.turn_index.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    async def get_latest_summary(self, session_id: str) -> ChatSummary | None:
        result = await self.db.execute(
            select(ChatSummary)
            .where(ChatSummary.session_id == session_id, ChatSummary.is_archived.is_(False))
            .order_by(ChatSummary.version.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def get_memory(self, session_id: str, limit: int = 100) -> list[ChatMemoryKV]:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(ChatMemoryKV)
            .where(
                ChatMemoryKV.session_id == session_id,
                (ChatMemoryKV.expires_at.is_(None)) | (ChatMemoryKV.expires_at > now),
            )
            .order_by(ChatMemoryKV.importance.desc(), ChatMemoryKV.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def upsert_memory_entries(
        self,
        session_id: str,
        source_turn: int,
        entries: list[tuple[str, str, int]],
    ) -> int:
        if not entries:
            return 0
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=self.settings.kv_ttl_hours)
        written = 0
        for key, value, importance in entries:
            result = await self.db.execute(
                select(ChatMemoryKV).where(ChatMemoryKV.session_id == session_id, ChatMemoryKV.key == key).limit(1)
            )
            existing = result.scalars().first()
            if existing:
                existing.value = value
                existing.importance = max(existing.importance, importance)
                existing.source_turn = source_turn
                existing.expires_at = expires_at
            else:
                self.db.add(
                    ChatMemoryKV(
                        session_id=session_id,
                        key=key,
                        value=value,
                        importance=importance,
                        source_turn=source_turn,
                        expires_at=expires_at,
                    )
                )
            written += 1
        await self.db.commit()
        return written

    async def add_summary(
        self,
        session_id: str,
        summary_text: str,
        covered_until_turn: int,
        trace_id: str | None = None,
    ) -> ChatSummary:
        latest = await self.get_latest_summary(session_id)
        version = (latest.version + 1) if latest else 1
        summary = ChatSummary(
            session_id=session_id,
            summary_text=summary_text,
            summary_short=summary_text[:400],
            covered_until_turn=covered_until_turn,
            token_estimate=estimate_tokens(summary_text),
            version=version,
            trace_id=trace_id,
        )
        self.db.add(summary)
        await self.db.commit()
        await self.db.refresh(summary)
        return summary

    async def should_refresh_summary(self, session_id: str) -> bool:
        latest = await self.get_latest_summary(session_id)
        covered_until = latest.covered_until_turn if latest else 0

        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.turn_index > covered_until, ChatMessage.is_archived.is_(False))
            .order_by(ChatMessage.turn_index.asc())
        )
        uncovered = list(result.scalars().all())
        if not uncovered:
            return False

        token_sum = sum(m.token_estimate for m in uncovered)
        return len(uncovered) >= self.settings.summary_trigger_turns or token_sum >= self.settings.summary_trigger_token_threshold

    async def build_summary_text(self, session_id: str) -> tuple[str, int]:
        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.is_archived.is_(False))
            .order_by(ChatMessage.turn_index.asc())
        )
        messages = list(result.scalars().all())
        if not messages:
            return "No history yet.", 0

        lines: list[str] = []
        for msg in messages[-24:]:
            prefix = msg.role.upper()
            lines.append(f"{prefix}: {msg.content[:220]}")
        summary_text = "Session summary:\n" + "\n".join(lines)
        covered_until_turn = messages[-1].turn_index
        return summary_text, covered_until_turn

    async def extract_memory_from_messages(self, session_id: str, since_turn: int = 0) -> list[tuple[str, str, int]]:
        result = await self.db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.turn_index > since_turn,
                ChatMessage.is_archived.is_(False),
                ChatMessage.role.in_(["user", "assistant"]),
            )
            .order_by(ChatMessage.turn_index.asc())
        )
        messages = list(result.scalars().all())
        entries: dict[str, tuple[str, int]] = {}
        for msg in messages:
            text = msg.content.strip()
            # simple key-value heuristic: "key: value"
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()
                if not key or not value:
                    continue
                if len(key) > 120:
                    continue
                importance = 3 if msg.role == "user" else 2
                entries[key] = (value[:1000], importance)
        return [(k, v, imp) for k, (v, imp) in entries.items()]

    async def retention_cleanup(self, session_id: str) -> dict[str, int]:
        stats = {"archived_messages": 0, "archived_summaries": 0, "deleted_memory": 0}
        now = datetime.now(timezone.utc)

        # expire memory first
        expired_result = await self.db.execute(
            select(ChatMemoryKV).where(ChatMemoryKV.session_id == session_id, ChatMemoryKV.expires_at.is_not(None), ChatMemoryKV.expires_at <= now)
        )
        expired_items = list(expired_result.scalars().all())
        for item in expired_items:
            await self.db.delete(item)
            stats["deleted_memory"] += 1

        # keep memory cap
        memory_result = await self.db.execute(
            select(ChatMemoryKV)
            .where(ChatMemoryKV.session_id == session_id)
            .order_by(ChatMemoryKV.importance.desc(), ChatMemoryKV.updated_at.desc())
        )
        memory_items = list(memory_result.scalars().all())
        if len(memory_items) > self.settings.max_kv_per_session:
            for item in memory_items[self.settings.max_kv_per_session :]:
                await self.db.delete(item)
                stats["deleted_memory"] += 1

        # archive old messages beyond soft limit
        message_result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.turn_index.desc())
        )
        messages = list(message_result.scalars().all())
        if len(messages) > self.settings.max_messages_per_session:
            keep_ids = {m.id for m in messages[: self.settings.max_messages_per_session]}
            for msg in messages[self.settings.max_messages_per_session :]:
                if not msg.is_archived:
                    msg.is_archived = True
                    stats["archived_messages"] += 1
                if msg.id in keep_ids:
                    msg.is_archived = False

        # keep latest K active summaries
        summary_result = await self.db.execute(
            select(ChatSummary)
            .where(ChatSummary.session_id == session_id)
            .order_by(ChatSummary.version.desc())
        )
        summaries = list(summary_result.scalars().all())
        for idx, summary in enumerate(summaries):
            should_archive = idx >= self.settings.max_summary_versions
            if summary.is_archived != should_archive:
                summary.is_archived = should_archive
                if should_archive:
                    stats["archived_summaries"] += 1

        await self.db.commit()
        return stats

    async def diagnostics(self, session_id: str) -> dict[str, int | None]:
        message_count = (
            await self.db.execute(select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id))
        ).scalar_one()
        active_message_count = (
            await self.db.execute(
                select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id, ChatMessage.is_archived.is_(False))
            )
        ).scalar_one()
        archived_message_count = message_count - active_message_count
        memory_count = (
            await self.db.execute(select(func.count(ChatMemoryKV.id)).where(ChatMemoryKV.session_id == session_id))
        ).scalar_one()
        latest = await self.get_latest_summary(session_id)
        return {
            "message_count": int(message_count),
            "active_message_count": int(active_message_count),
            "archived_message_count": int(archived_message_count),
            "memory_count": int(memory_count),
            "latest_summary_version": latest.version if latest else None,
            "latest_summary_covered_until_turn": latest.covered_until_turn if latest else None,
        }

    async def create_session_run(
        self,
        *,
        task_id: str,
        session_id: str,
        tenant_id: str,
        user_id: int | None,
        trace_id: str | None,
        agent_type: str,
        model: str | None,
        context_policy: str,
        turn_index: int | None,
        resolved_skills: list[str] | None = None,
        context_pack_ids: list[str] | None = None,
        routing_json: dict | None = None,
    ) -> SessionRun:
        now = datetime.now(timezone.utc)
        run = SessionRun(
            id=task_id,
            session_id=session_id,
            turn_index=turn_index,
            user_id=user_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            agent_type=agent_type,
            model=model,
            context_policy=context_policy,
            status="running",
            resolved_skills=resolved_skills or [],
            context_pack_ids=context_pack_ids or [],
            routing_json=routing_json,
            started_at=now,
        )
        self.db.add(run)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def complete_session_run(
        self,
        *,
        task_id: str,
        status: str,
        usage_json: dict | None = None,
        plan_json: dict | None = None,
        error_message: str | None = None,
    ) -> SessionRun | None:
        result = await self.db.execute(select(SessionRun).where(SessionRun.id == task_id))
        run = result.scalars().first()
        if run is None:
            return None
        run.status = status
        run.usage_json = usage_json
        run.plan_json = plan_json
        run.error_message = error_message
        run.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def list_session_runs(self, session_id: str, limit: int = 50) -> list[SessionRun]:
        result = await self.db.execute(
            select(SessionRun)
            .where(SessionRun.session_id == session_id)
            .order_by(SessionRun.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_tool_calls(self, session_id: str, limit: int = 100) -> list[ChatToolCall]:
        result = await self.db.execute(
            select(ChatToolCall)
            .where(ChatToolCall.session_id == session_id)
            .order_by(ChatToolCall.turn_index.asc(), ChatToolCall.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_entities(self, session_id: str, active_only: bool = False) -> list[SessionEntity]:
        stmt = select(SessionEntity).where(SessionEntity.session_id == session_id).order_by(SessionEntity.updated_at.desc())
        if active_only:
            stmt = stmt.where(SessionEntity.is_active.is_(True))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _aggregate_usage_from_runs(runs: list[SessionRun], message_token_estimate: int) -> TokenUsageBreakdown:
        input_tokens = 0
        output_tokens = 0
        for run in runs:
            usage = run.usage_json or {}
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
        total = input_tokens + output_tokens
        if total == 0:
            total = message_token_estimate
        return TokenUsageBreakdown(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            message_token_estimate=message_token_estimate,
            run_count=len(runs),
        )

    async def get_session_token_usage(self, session_id: str) -> TokenUsageBreakdown:
        runs = await self.list_session_runs(session_id, limit=500)
        message_sum = (
            await self.db.execute(
                select(func.coalesce(func.sum(ChatMessage.token_estimate), 0)).where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.is_archived.is_(False),
                )
            )
        ).scalar_one()
        return self._aggregate_usage_from_runs(runs, int(message_sum or 0))

    async def get_user_token_usage(
        self,
        tenant_id: str,
        user_id: int,
        session_limit: int = 50,
    ) -> tuple[TokenUsageBreakdown, list[tuple[str, TokenUsageBreakdown]]]:
        sessions = await self.list_sessions(tenant_id=tenant_id, user_id=user_id, limit=session_limit)
        by_session: list[tuple[str, TokenUsageBreakdown]] = []
        total = TokenUsageBreakdown()
        for session in sessions:
            usage = await self.get_session_token_usage(session.id)
            by_session.append((session.id, usage))
            total.input_tokens += usage.input_tokens
            total.output_tokens += usage.output_tokens
            total.total_tokens += usage.total_tokens
            total.message_token_estimate += usage.message_token_estimate
            total.run_count += usage.run_count
        return total, by_session


def normalize_context_policy(policy: str | None) -> ContextPolicy:
    if policy in {"balanced", "recent_first", "summary_heavy"}:
        return policy
    return "balanced"
